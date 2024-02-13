import logging
import string
import socket
import re
import shutil

from random import SystemRandom, randint
from argparse import Namespace
from datetime import datetime
from os import environ
from platform import system
from time import perf_counter, sleep
from tempfile import gettempdir
from pathlib import Path

import numpy as np
from psutil import Popen, subprocess
from steamid import SteamID

# TODO: Undo monkey patch when pull request is merged: https://github.com/ValvePython/vdf/pull/53
from . import vdf_patch
import vdf

from .game import Game

if system().startswith("Linux"):
    import control


class Test:
    """
    Takes care of controlling the game and capture software, and applying changes
    """

    required_mangohud_conf = ("no_display=0", "log_interval=0", "control=mangohud")

    required_presentmon_conf = ("-terminate_after_timed", "-stop_existing_session")

    required_launch_options = (
        "+con_logfile",
        "demoknight.log",
        "-usercon",
        "-condebug",
        "-conclearlog",
        # "+developer", "1", "+alias", "developer",
        # "+contimes", "0", "+alias", "contimes",
        "+ip",
        "0.0.0.0",
        "+alias",
        "ip",
        "+sv_rcon_whitelist_address",
        "127.0.0.1",
        # "+rcon_password", self._rand_pass(), "+alias", "rcon_password",
        # "+hostport", self._free_port(), "+alias", "hostport",
        # "+net_start",
        # "+con_timestamp", "1", "+alias", "con_timestamp",
        # "+net_showmsg", "svc_UserMessage",
        # "+alias", "net_showmsg"
    )

    game_environ = environ.copy()
    # game_environ.update({"MANGOHUD": "1"})
    # game_environ.update({"GAME_DEBUGGER": "mangohud"})

    def __init__(self, args, index):
        self.name = args.tests[index]["name"]
        self.results = []
        self.index = index
        self.temp_dir = Path(gettempdir()) / "demoknight"
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def capture(self, args):
        # create independent process just to make sure
        # https://stackoverflow.com/questions/13243807/popen-waiting-for-child-process-even-when-the-immediate-child-has-terminated/13256908#13256908

        test_launch_options = args.tests[self.index]["changes"].get(
            "launch-options", ()
        )

        all_launch_options = (
            args.launch_options
            + Test.required_launch_options
            + (
                "+rcon_password",
                Test._rand_pass(),
                "+alias",
                "rcon_password",
                "+hostport",
                str(Test._free_port()),
                "+alias",
                "hostport",
                "+net_start",
            )
            + tuple(i for i in test_launch_options if i is not None)
        )

        kwargs = {}

        if system().startswith("Win"):
            # from msdn [1]
            NEW_PROCESS_GROUP = 0x00000200
            DETACHED_PROCESS = 0x00000008
            kwargs.update(creationflags=DETACHED_PROCESS | NEW_PROCESS_GROUP)
            kwargs.update(env=environ.copy())

        elif system().startswith("Linux"):
            specific_mangohud_conf = (
                f"log_duration={args.duration + args.start_buffer}",
                (
                    "output_folder"
                    f"={args.raw_path.absolute() / args.output_file / self.name}"
                ),
            )
            mangohud_conf = "\n".join(
                Test.required_mangohud_conf + specific_mangohud_conf
            )
            temp_conf_dir = self.temp_dir / "MangoHud.conf"
            with open(temp_conf_dir, "w") as conf_file:
                conf_file.write(mangohud_conf)
            kwargs.update(start_new_session=True)

            specific_environ = Test.game_environ.copy()
            # specific_environ.update({"MANGOHUD_CONFIG": mangohud_conf})
            specific_environ.update({"MANGOHUD_CONFIGFILE": temp_conf_dir})
            kwargs.update(env=specific_environ)
        else:
            raise NotImplementedError(
                "Unsupported OS, this tool is only available for Windows and Linux"
            )

        local_conf_path = Test._get_user_local_config_path(args.steam_path, args.gameid)
        local_config = Test._try_parsing_vdf(local_conf_path)

        user_launch_options = local_config["UserLocalConfigStore"]["Software"]["Valve"][
            "Steam"
        ]["apps"][str(args.gameid)]["LaunchOptions"]
        (
            user_env,
            user_commands,
            user_game_args,
        ) = Test._separate_launch_options_elements(user_launch_options)

        local_config["UserLocalConfigStore"]["Software"]["Valve"]["Steam"]["apps"][
            str(args.gameid)
        ]["LaunchOptions"] = " ".join(
            user_env
            + (f"MANGOHUD_CONFIGFILE={temp_conf_dir}", "mangohud")
            + user_commands
            + ("%command%",)
            + user_game_args
            + all_launch_options
        )

        # Backup local conf
        local_conf_bak = local_conf_path.parent / "localconfig.vdf.bak"
        if local_conf_bak.exists():
            local_conf_bak.unlink()

        shutil.copy2(local_conf_path, local_conf_bak)

        # TODO: This catch is extremely broad, but I'm terrified of losing people's files
        try:
            # Dump modified conf
            vdf.dump(local_config, open(local_conf_path, "w"), pretty=True)

            # Start game and wait for it to finish loading
            gm = Game(
                gameid=args.gameid,
                game_path=args.tests[self.index].get("game-path") or args.game_path,
                steam_path=args.steam_path,
                l_opts=all_launch_options,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **kwargs,
            )

            # Due to all of the crazy shit people make run when the game starts,
            # can't think of a better way to detect if they have finished running
            # than this
            # TODO: Include this in Game.update_state() instead
            tictoc = []
            for _ in range(200):
                tic = perf_counter()
                gm.rcon("echo Waiting for responsiveness")
                toc = perf_counter()
                diff = toc - tic
                logging.debug(f"Rcon response delay: {diff}")
                sleep(0.1)
                tictoc.append(diff)
                mean = np.mean(tictoc[-50:])
                if len(tictoc) > 50 and abs(mean - diff < diff * 0.01):
                    break

            for i in range(args.passes):
                # Apply cvars for each test
                for ch in args.tests[self.index]["changes"].get("cvars", []):
                    gm.rcon(ch)

                # Play demo and wait for game to load
                gm.playdemo(args.demo_path)

                if args.start_tick - args.start_buffer * (1 / args.tick_interval) < 15:
                    raise Exception(
                        "Due to constraints with frametime capture and demos, minimum"
                        f" value for -s/--start-tick is {15 + args.start_buffer}"
                    )

                # Go to tick and wait for fast-foward to finish
                while True:
                    try:
                        gm.gototick(
                            int(
                                args.start_tick
                                - args.start_buffer * (1 / args.tick_interval)
                            ),
                            args.tick_interval,
                        )
                        break
                    except TimeoutError as e:
                        logging.error(e)
                        gm.rcon("disconnect")
                        gm.playdemo(args.demo_path)
                        continue
                    except RuntimeError as e:
                        logging.critical(e)
                        gm.rcon("disconnect")
                        gm.playdemo(args.demo_path)
                        continue
                gm.not_capturing.clear()
                if system().startswith("Win"):
                    specific_presentmon_conf = (
                        "-timed",
                        str(args.duration + args.start_buffer),
                        "-process_id",
                        str(gm.pid),
                        "-output_file",
                        str(
                            args.raw_path.absolute()
                            / args.output_file
                            / self.name
                            / f"PresentMon-{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.csv"
                        ),
                    )
                    Popen(
                        (args.presentmon_path,)
                        + specific_presentmon_conf
                        + Test.required_presentmon_conf
                    )

                if system().startswith("Linux"):
                    control.control(
                        Namespace(cmd="start-logging", socket="mangohud", info="")
                    )

                # Extend duration due to mangohud bug
                sleep(args.duration + args.start_buffer)
                gm.not_capturing.set()
                sleep(0.5)

                # Player animations seem to glitch out if I don't disconnect
                # before doing "playdemo"
                gm.rcon("disconnect")
                sleep(0.5)

                p = args.raw_path.absolute() / args.output_file / self.name
                logs = list(p.glob("./*[0-9].csv"))
                logs.sort(key=lambda x: x.stat().st_mtime, reverse=True)

                self.results.append(logs[0])
                print(f"Finished pass {i}")

            gm.quit()
        except Exception as e:
            # restore local conf
            logging.critical("Test failed, restoring modified")
            if local_conf_path.exists():
                local_conf_path.unlink()

            shutil.copy2(local_conf_bak, local_conf_path)
            raise

        # restore local conf
        if local_conf_path:
            local_conf_path.unlink()

        shutil.copy2(local_conf_bak, local_conf_path)
        sleep(10)

    @staticmethod
    def _rand_pass():
        ch = string.ascii_letters + string.digits
        password = "".join(SystemRandom().choice(ch) for _ in range(randint(6, 10)))
        logging.debug(f"rcon password: {password}")
        return password

    @staticmethod
    def _free_port():
        for _ in range(10):
            cur_port = randint(10240, 65534)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if s.connect_ex(("localhost", cur_port)):
                    logging.debug(f"rcon port: {cur_port}")
                    return cur_port
            raise ValueError("Could not find empty port for rcon after 10 retries")

    @staticmethod
    def _get_user_local_config_path(steam_path, gameid):
        if system().startswith("Win"):
            # TODO
            return ""
        # Steam running + most recent profile found most likely means he is logged
        # in on this account right now
        elif system().startswith("Linux"):
            loginusers = Test._try_parsing_vdf(steam_path / "config" / "loginusers.vdf")
            steam_user = {"Timestamp": 0}
            for steamid64, info in loginusers["users"].items():
                newer = int(info["Timestamp"]) > steam_user["Timestamp"]
                if info["MostRecent"] == 1 or newer:
                    steam_user["AccountID"] = SteamID(steamid64).accountid
                    steam_user["AccountName"] = info["AccountName"]
                    steam_user["Timestamp"] = int(info["Timestamp"])
            if steam_user["Timestamp"] > 0:
                logging.info(
                    f"Found most recent Steam account used: {steam_user['AccountName']}"
                )
            else:
                logging.critical(
                    "Could not find any recently logged in steam accounts, make sure you"
                    " are logged in or use --nosteam"
                )
                sys.exit(2)
            account_id = steam_user["AccountID"]

            local_config_path = (
                steam_path / "userdata" / str(account_id) / "config/localconfig.vdf"
            )
            return local_config_path

    @staticmethod
    def _try_parsing_vdf(path):
        file_type = str(path).split(".")[-1]
        if file_type in ("vdf", "acf"):
            try:
                with open(path, encoding="utf-8") as cur_file:
                    return vdf.load(cur_file)
            except OSError as e:
                raise Exception(f"Cannot find {path} \n{e}") from e
            except ValueError as e:
                raise Exception(f"Cannot parse {path} as {file_type} file\n{e}") from e
        else:
            raise ValueError("File type unknown")

    @staticmethod
    def _separate_launch_options_elements(string):
        # Regular expression patterns
        env_variable_pattern = r"\w+=\S+"
        command_pattern = r"[^%]+(?=%command%)"
        parameters_pattern = r"%command%\s*(.*)"

        # Extracting environment variables
        env_variables = tuple(
            re.findall(env_variable_pattern, string) if "%command%" in string else []
        )

        # Extracting commands
        commands = tuple(
            re.findall(command_pattern, string) if "%command%" in string else []
        )

        # Extracting parameters
        parameters = tuple(
            re.findall(parameters_pattern, string)
            if "%command%" in string
            else str.split(string, sep=" ")
        )

        return env_variables, commands, parameters
