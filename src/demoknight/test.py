import logging
import string
import socket
import re
import shutil

from random import SystemRandom, randint
from argparse import Namespace
from datetime import datetime
from os import environ, rename, path
from platform import system
from time import perf_counter, sleep
from tempfile import gettempdir
from pathlib import Path

import numpy as np
from psutil import Popen, subprocess

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

    # TODO: File bugreport about mangohud failing to capture with no_display=1
    required_mangohud_conf = (
        "no_display=0",
        "log_interval=0",
        "control=mangohud",
        "fps=0",
        "frame_timing=0",
        "cpu_stats=0",
        "gpu_stats=0",
    )

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
    game_environ.update({"GAME_DEBUGGER": "mangohud"})

    def __init__(self, args, index):
        self.name = args.tests[index]["name"]
        self.results = []
        self.index = index
        self.temp_dir = Path(gettempdir()) / "demoknight"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.curr_pass = 0

    def capture(self, args):
        self.curr_pass = 0
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
            specific_environ.update({"MANGOHUD_CONFIGFILE": str(temp_conf_dir)})
            kwargs.update(env=specific_environ)
        else:
            raise NotImplementedError(
                "Unsupported OS, this tool is only available for Windows and Linux"
            )

        test_launch_options = args.tests[self.index]["changes"].get(
            "launch-options", ()
        )
        # Move files for this test and back up if it already exists
        for path in args.tests[self.index].get("changes", {}).get("paths", {}):
            source_path = Path(path["from"])
            destination_path = Path(path["to"])

            if source_path.is_dir():
                # Copy directory tree
                if destination_path.exists():
                    if not destination_path.is_dir():
                        raise ValueError(
                            "Paths in tests must contain either 2 directory paths, or 2 file paths, separated by space"
                        )
                    # If the destination directory already exists, create a backup by renaming it
                    backup_destination = destination_path.with_name(
                        destination_path.name + ".bak"
                    )
                    shutil.move(destination, backup_destination)
                    logging.info(f"Copied existing directory to: {backup_destination}")

                # Copy the source directory to the destination
                shutil.copytree(source_path, destination_path)
                logging.info(
                    f"Moved folder/file from {source_path} to {destination_path}"
                )
            else:
                # Copy file
                if destination_path.exists():
                    # If the destination file already exists, create a backup by renaming it
                    backup_destination = destination_path.with_name(
                        destination_path.name + ".bak"
                    )
                    destination_path.rename(backup_destination)
                    logging.info(f"Copied existing file to: {backup_destination}")

                # Copy the source file to the destination
                shutil.copy(source_path, destination_path)
                logging.info(
                    f"Moved folder/file from {source_path} to {destination_path}"
                )

            with open(destination_path.parent / "demoknight.lock", "a") as lock_file:
                lock_file.write(str(destination_path) + "\n")

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
            if logs[0] not in self.results:
                self.results.append(logs[0])
                self.curr_pass += 1
            else:
                gm.quit()
                raise FileNotFoundError(
                    "Mangohud did not generate a new file after the pass was done. Closing the game and retrying the entire test."
                )
            print(f"Finished pass {i}")

        gm.quit()
        for path in args.tests[self.index].get("changes", {}).get("paths", {}):
            destination_path = Path(path["to"])

            # Check if a backup exists
            backup_destination = destination_path.with_name(
                destination_path.name + ".bak"
            )
            if backup_destination.exists():
                # Restore the backup
                backup_destination.rename(destination_path)
                logging.info(f"Reverted changes. Restored backup: {destination_path}")

            else:
                # Delete the newly copied file or directory
                if destination_path.exists():
                    if destination_path.is_dir():
                        shutil.rmtree(path["to"])
                        logging.info(
                            f"Reverted changes. Deleted directory: {destination_path}"
                        )
                    else:
                        destination_path.unlink()
                        logging.info(
                            f"Reverted changes. Deleted file: {destination_path}"
                        )
            lock_file = destination_path.parent / "demoknight.lock"
            lock_file.unlink()

        sleep(10)

    @staticmethod
    def _check_paths(path):
        path = {k: Path(p) for k, p in zip(("from", "to"), (path["from"], path["to"]))}
        if not path["to"].parent.exists():
            raise FileNotFoundError(f"Path {Path(paths['to']).parent} does not exist.")
        if not path["from"].exists():
            raise FileNotFoundError(f"Path {Path(path['from'])} does not exist.")
        if not all(p.is_absolute() for _, p in path.items()):
            raise ValueError(f"Paths in tests must be absolute")

        lock_file = path["to"].parent / ("demoknight.lock")
        if lock_file.exists():
            logging.critical(f"{lock_file} found, restoring original paths")
            with open(lock_file, "r") as file:
                for line in file:
                    line_path = Path(line.strip())
                    line_backup = line_path.with_name(line_path.name + ".bak")
                    if line_backup.exists():
                        logging.critical(
                            f"{line_backup} found, restoring original file"
                        )
                        if line_backup.is_dir():
                            shutil.move(line_backup, line_path)
                        else:
                            line_backup.rename(line_path)
                        logging.info(f"Reverted changes. Restored backup: {line_path}")
                    else:
                        if line_path.is_dir():
                            shutil.rmtree(line_path)
                        else:
                            line_path.unlink()
            lock_file.unlink()

        if path["to"].exists():
            if not all((v.is_file()) for _, v in path.items()) or all(
                (not v.is_file()) for _, v in path.items()
            ):
                raise ValueError(
                    "Paths in tests must contain either 2 directory paths, or 2 file paths, separated by space"
                )
        return path

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
