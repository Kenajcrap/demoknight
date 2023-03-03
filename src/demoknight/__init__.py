import argparse
import json
import csv
import os
import platform
import socket
import string
import sys
import re
import logging
from datetime import datetime
from enum import Enum
from pathlib import Path
from random import SystemRandom, randint
from subprocess import PIPE, Popen
from tempfile import gettempdir
from time import perf_counter, sleep

import numpy as np
import psutil
import vdf
from rcon.source import Client
from rcon.exceptions import EmptyResponse
from steamid import SteamID
from watchfiles import watch

if platform.system() == "Linux":
    import control

if platform.system() == "Windows":
    import winreg  # pylint: disable=import-error


def main():
    argv = sys.argv[1:]
    config_parser = argparse.ArgumentParser(
        description=("A tool that operates mangohud together with rcon to "
                     "automatically generate comparative benchmark results "
                     "for different game setups"),
        prog="DemoKnight",
        prefix_chars='-',
        add_help=False)

    # JSON support
    config_parser.add_argument(
        "-j", "--job-file",
        help=("Path to json file containing config parameters and a list of "
              "parameters to be tested. Currently supports cvars and launch "
              "options to be applied"))
    args, rest_argv = config_parser.parse_known_args()
    if args.job_file:
        json_dict = try_parsing_file(args.job_file)
        vars(args).update(json_dict)
    else:
        json_dict = {"tests": []}
        vars(args).update(json_dict)

    parser = argparse.ArgumentParser(
        parents=[config_parser],
        allow_abbrev=False,
        prefix_chars='-')

    argv_and_parsed = argv+list(json_dict.keys())

    parser.add_argument(
        "-g", "--gameid",
        required=not [x for x in [
            "-G", "--game-path", "gameid", "game_path"
            ] if x in argv_and_parsed],
        default=0,
        type=int,
        help=("Overrides the gameid used to launch the game, game will be run "
              "through Steam."))

    parser.add_argument(
        "-G", "--game-path",
        required=not [x for x in [
            "-g", "--gameid", "game_path", "gameid"
            ] if x in argv_and_parsed],
        help=("Overrides the path to the game's binary file or starting "
              "script, game will not be run through Steam."))

    parser.add_argument(
        "-l", "--launch-options",
        default="",
        nargs="+",
        help=("Additional launch options to use for every test, added to the "
              "ones gotten from steam if using --gameid. If using --gamepath, "
              "don't forget required launch options like '-game'"))

    parser.add_argument(
        "-o", "--output-path",
        type=Path,
        default=str(Path(gettempdir()) / "demoknight"),
        help=("Path to the mangohud/presentmon log files. Defaults to the temporary "
              "folder of your OS."))

    parser.add_argument(
        "-n", "--passes",
        default=5,
        type=int,
        help="Overrides the number of passes. Default: 5")

    parser.add_argument(
        "-k", "--keep-first-pass",
        default=False,
        action=argparse.BooleanOptionalAction,
        help=("Keep first pass. Discarting the first pass is needed if "
              "the demo section used for benchmark is the very start, "
              "since performance there is not representative."))

    parser.add_argument(
        "-s", "--start-tick",
        default=(66*2)+20,
        type=int,
        help="Start of the benchmark section of the demo in ticks.")

    parser.add_argument(
        "-d", "--duration",
        default=20.0,
        type=float,
        help="Benchmark duration in seconds. Default: 20.")

    parser.add_argument(
        "-t", "--tickrate",
        default=66.6,
        type=float,
        help="Server tickrate of the demo being played. Default: 66.6")

    parser.add_argument(
        "-D", "--demo-path",
        required=True,
        help=("Path to the demo file, starting from the game's 'mod' "
              "directory (same as the 'playdemo' console command in-game)."))

    parser.add_argument(
        "-b", "--baseline",
        default=True,
        action=argparse.BooleanOptionalAction,
        help=("Whether or not to capture a baseline run without applying configs"))

    parser.add_argument(
        "--name",
        default=str(datetime.now().replace(microsecond=0)),
        help=("Name of the generated summary file. Default: current date and time"))

    parser.add_argument(
        "-v", "--verbosity",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help=("Logging verbosity. Default: WARNING"))

    parser.add_argument(
        "--presentmon-path",
        default="presentmon",
        help=("Path to PresentMon executable. Default: 'presentmon'"))

    parser.add_argument(
        "-f", "--format",
        default="csv",
        choices=["csv", "json"],
        help=("Format of the output file. Default: csv"))

    parser.add_argument(
        "tests",
        action=SplitArgs,
        nargs="*",
        help=("Space separated inline test list instead of reading from json "
              "(one item per test). Options starting with \"+\" will be "
              "treated as cvars. Options starting with \"_\" will be treated "
              "as launch options."))

    parser.add_argument(
        "--start-delay",
        default=2,
        type=float,
        help=("Fastfowarding causes particles and props to behave unlike they do "
              "normally and cause lag once we resume normal playback. We gotta let "
              "those settle down before we can get good data."))

    parser.parse_args(args=rest_argv, namespace=args)

    numeric_loglevel = getattr(logging, args.verbosity.upper(), None)

    # Mangohud doesn't start capturing at the start as soon as we tell it to.
    # Therefore we gotta start a second earlier to be safe
    # Fast fowarding causes particles and props to behave unlike they do
    # normally and cause lag once we resume normal playback. We gotta let those
    # settle down before we can get good data.

    # TODO: Check if tick is at the very start of a demo and refrain from
    # fast-fowarding instead.
    start_delay = 2*args.tickrate
    buffer_delay = 1*args.tickrate
    start_tick = round(args.start_tick - start_delay)
    buffer_tick = round(args.start_tick - start_delay - buffer_delay)

    if start_tick < 15:
        parser.error("Due to constraints with frametime capture and demos, minimum "
                     "value for -s/--start-tick is " + str(15
                                                           + start_delay/args.tickrate
                                                           + buffer_delay/args.tickrate)
                     )

    if not isinstance(numeric_loglevel, int):
        raise ValueError('Invalid log level: %s' % args.verbosity)
    logging.basicConfig(level=numeric_loglevel)

    # Create log folder if it doesn't exist
    try:
        args.output_path.absolute().mkdir()
    except FileExistsError:
        pass

    mangohud_cfg = [
        "no_display=0",
        "log_interval=0",
        "control=mangohud",
        "log_duration=" + str(args.duration+2.0),
        # For some reason Mangohud has started to not capture the first few
        # hundred miliseconds, but finishes capturing exactly at the right
        # time. For now I will simply extend the time captured by 1 second
        # "backwards", then discard the first second of capture when rendering
        # results TODO: File an issue in their github
        "output_folder=" + str(args.output_path.absolute())
    ]

    # Check if steam is setup correctly, only start job if it is not running or
    # if it has the correct mangohud config already

    if args.gameid:
        logging.info("Checking if steam has the right enviroment variables set")
        steam_state = get_steam_state(mangohud_cfg)
        logging.debug("Steam State: " + str(steam_state.value))

        if steam_state in (SteamState.NOT_STARTED, SteamState.CONFIGURED):
            print("Starting job")
        elif steam_state == SteamState.NO_MANGOHUD:
            raise OSError("Steam is running but does not have the right GAME_DEBUGGER, "
                          "please close Steam so it can be opened with the correct "
                          "launch options")

        # Due to a vulnerability related to using the steam protocol through a
        # browser, steam has disabled the ability to use %command% in launch
        # options using the steam command

        # So to launch the game with mangohud, it's easier if we just launch
        # steam with mangohud instead. Another option is to instead append the
        # vtf file that handles launch options.
        elif steam_state == SteamState.NOT_CONFIGURED:
            raise OSError("Steam is running but does not have the "
                          "right MANGOHUD_CONFIG, please close Steam so it can be "
                          "opened with the correct launch options")
        else:
            raise OSError("unknown steam_state")

        args.game_path = find_game_dir(find_steam_dir(), str(args.gameid))

    # Include a job at the top of the list for baseline if required
    if args.baseline:
        args.tests.insert(0, {})

    # Main testing loop

    # Estimate how much time the job will take and warn the user if more than 1 hour
    eta = ((10
            + 5
            + (start_delay/args.tickrate)
            + (buffer_delay/args.tickrate)
            + args.duration
            + (buffer_tick/args.tickrate/20))
           * (args.passes) * 1.2)
    logging.info("ETA: " + str(eta))
    if eta > 3600:
        logging.warning("This job may take more than " + str(round((eta/60/60), 2))
                        + " to complete. Consider breaking it up into multiple jobs to "
                        "avoid having to redo due to crashes or other problems")

    for test in args.tests:
        port = find_free_random_port()
        password = generate_random_password()
        # Clear log file after each run given we are spamming it so much
        p = Path(args.game_path)
        logs = list(p.glob("./*/demoknight.log"))
        if len(logs) > 1:
            raise FileNotFoundError("More than one demoknight.log file "
                                    "was found")
        if logs:
            log_path = logs[0]
            try:
                logging.info("Removing " + str(log_path))
                os.remove(log_path)
            except FileNotFoundError:
                logging.info(str(log_path) + " doesn't exists, continuig.")

        add_options = (
            args.launch_options.split()
            + test["launch_options"] if "launch_options" in test else [])
        # TODO: Find out why -novid is causing problems
        launch_game(int(args.gameid) or args.game_path, str(port),
                    password, mangohud_cfg,
                    additional_launch_options=add_options)
        sleep(5)
        # TODO: Find a non-intrusive way to detect once the game has loaded all
        # of the custom stuff players do, and only run after that
        rcon_command(password, port, ["echo", "Starting", "Autobenchmark"])

        # Due to all of the crazy shit people make run when the game starts,
        # can't think of a better way to detect if they have finished running
        # than this
        tictoc = []
        for _ in range(200):
            tic = perf_counter()
            rcon_command(
                password, port,
                ["echo", "waiting", "for", "responsiveness"])
            toc = perf_counter()
            diff = toc-tic
            logging.debug("Rcon response delay:" + str(diff))
            sleep(0.1)
            tictoc.append(diff)
            mean = np.mean(tictoc[-20:])
            if len(tictoc) > 20 and abs(mean - diff < diff*0.01):
                break

        # Some cvars might not apply correctly if the player is using
        # map_background
        rcon_command(password, port, ["disconnect"])
        sleep(0.5)
        if "cvar" in test:
            for cvar in test["cvar"]:
                rcon_command(password, port, ["echo"] + cvar.split(" "))
                rcon_command(password, port, cvar.split(" "))
                sleep(0.5)
        lastpos = 0
        for _ in range(args.passes):
            rcon_command(password, port, ["playdemo", args.demo_path])
            sleep(1)
            rcon_command(
                password, port,
                ["echo", "Starting", "Demo"])
            rcon_command(
                password, port,
                ["demo_debug", "1"])
            rcon_command(
                password, port,
                ["demo_gototick", str(buffer_tick), "0", "0"])

            lastpos = wait_for_console(r'Demo message, tick ' + str(buffer_tick+5),
                                       log_path, lastpos)

            rcon_command(
                password, port,
                ["demo_debug", "0"])

            rcon_command(
                password, port,
                ["demo_timescale", "1", ";",
                 "demo_gototick", str(start_tick), "0", "0"])
            if platform.system() == "Windows":
                Popen([args.presentmon_path,
                       "-timed " + (args.duration + (start_delay/args.tickrate)),
                       "-terminate_after_timed",
                       "-output_file " + str(args.output_path.absolute())])
            if platform.system() == "Linux":
                control.control(argparse.Namespace(cmd="start-logging",
                                                   socket="mangohud",
                                                   info=""))

            # Extend duration due to mangohud bug
            sleep(args.duration+start_delay/args.tickrate)
            sleep(0.5)
            # Player animations seem to glitch out if I don't disconnect
            # before doing "playdemo"
            rcon_command(password, port, ["disconnect"])
            sleep(0.5)
        rcon_command(password, port, ["quit"])
        sleep(10)

    # Import mangohud data
    if platform.system() == "Windows":
        usecols = (7, 9)
        skiprows = 1
    elif platform.system() == "Linux":
        usecols = (1, 11)
        skiprows = 3
    summary = []
    p = args.output_path.absolute()
    logs = list(p.glob("./*[0-9].csv"))
    logs.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    for test in range(len(args.tests)):
        testname = " ".join(" ".join(x) for x in args.tests[-test-1].values())
        if testname == "":
            testname = "baseline"
        summary.append({"name": testname, "average": [], "variance": []})
        for i in range(args.passes):
            if i == 0 and not args.keep_first_pass:
                continue
            arr = np.loadtxt(
                logs[(test*args.passes)+i],
                delimiter=",",
                usecols=usecols,
                skiprows=skiprows)
            # We actually start capturing 2 seconds before we need to, so get
            # rid of those rows
            arr[:, 1] -= (start_delay/args.tickrate * 1000000000)
            arr = arr[arr[..., 1] >= 0]
            summary[test]["average"].append(np.average(arr[:, 0], axis=0))
            summary[test]["variance"].append(np.var(arr[:, 0], axis=0))
    summary.reverse()
    with open(args.name, "w", encoding="utf-8") as outfile:
        if args.format == "json":
            json.dump(summary, outfile)
        elif args.format == "csv":
            writer = csv.DictWriter(outfile, fieldnames=["name", "average", "variance"])
            for test in summary:
                test["name"] = [test["name"]]*len(test["average"])
                v2 = [dict(zip(test, t)) for t in zip(*test.values())]
                writer.writeheader()
                writer.writerows(v2)
    print("done")


class SplitArgs(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        tests = []
        last_item = []
        for i in values:
            if not last_item:
                last_item.append(i)
                continue
            if i[0] in ["_", "+"]:
                tests.append(self._make_test(last_item))
                last_item = [i]
            else:
                last_item.append(i)
        else:
            if values:
                tests.append(self._make_test(last_item))
        if values:
            setattr(namespace, self.dest, tests)

    def _make_test(self, test):
        prefix = test[0][0] or None
        if prefix == "+":
            return {"cvar": [" ".join(test)[1:]]}
        elif prefix == "_":
            return {"launch_options": "-" + " ".join(test)[1:]}


def wait_for_console(regex_pattern, log_path, last_position=0):  # GameID required
    # print(steamdir)
    logpat = re.compile(regex_pattern)
    for changes in watch(log_path):
        if last_position > os.path.getsize(log_path):
            last_position = 0
        with open(log_path) as f:
            f.seek(last_position)
            loglines = f.readlines()
            last_position = f.tell()
            groups = (logpat.search(line.strip()) for line in loglines)
            for g in groups:
                if g:
                    return last_position


def find_steam_dir():
    if platform.system() == "Linux":
        steam_dir = Path("~/.steam/steam").expanduser()
        if not Path(steam_dir).expanduser().exists():
            raise FileNotFoundError("Cannot find '~/.steam/steam', ensure Steam is "
                                    "installed or overwrite with -s")
    elif platform.system() == "Windows":
        reg_path = "SOFTWARE\\Valve\\Steam\\ActiveProcess"
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path)
        except OSError as e:
            raise Exception("Cannot find required registry key, ensure Steam is "
                            "installed for the current user or overwrite with -s\n"
                            + str(e)) from e
        try:
            steam_dll_reg = winreg.QueryValueEx(key, "SteamClientDLL")
            steam_dir = Path(steam_dll_reg[0].rsplit("\\", maxsplit=1)[0])
        except OSError as e:
            raise Exception("Cannot find path to Steam installation, ensure Steam is "
                            "installed or overwrite with -s\n"
                            + str(e)) from e
    else:
        raise NotImplementedError("Unsupported OS, this tool is only available for "
                                  "Windows and Linux")
    return steam_dir


def find_game_dir(steam_dir, game_id):
    libraries = try_parsing_file(steam_dir / "steamapps/libraryfolders.vdf")

    for k, v in libraries["libraryfolders"].items():
        if not k.isnumeric():
            continue
        elif str(game_id) in v["apps"].keys():
            game_library_dir = Path(v["path"])
            break
    else:
        raise FileNotFoundError("Cannot find path to steam library containing the game,"
                                " ensure the game is installed or overwrite with "
                                "--game_path")

    appmanifest_file_path = (game_library_dir /
                             ("steamapps") /
                             ("appmanifest_" + game_id + ".acf"))

    appmanifest = try_parsing_file(appmanifest_file_path)

    game_dir = (game_library_dir /
                "steamapps" /
                "common" /
                appmanifest["AppState"]["installdir"])

    return game_dir


# Currently unused function, maybe useful later
def get_user_launch_options(steam_path, game_id):
    if platform.system() == "Windows":
        # TODO
        return ""
    # Steam running + most recent profile found most likely means he is logged
    # in on this account right now
    elif platform.system() == "Linux":
        loginusers = try_parsing_file(steam_path / "config" / "loginusers.vdf")
        steam_user = {"Timestamp": 0}
        for steamid64, info in loginusers["users"].items():
            newer = int(info["Timestamp"]) > steam_user["Timestamp"]
            if (info["MostRecent"] == 1 or newer):
                steam_user["AccountID"] = SteamID(steamid64).accountid
                steam_user["AccountName"] = info["AccountName"]
                steam_user["Timestamp"] = int(info["Timestamp"])
        if steam_user["Timestamp"] > 0:
            logging.info("Found most recent Steam account used: "
                         + steam_user["AccountName"])
        else:
            logging.critical("Could not find any recently logged in steam accounts, "
                             "make sure you are logged in or use --nosteam")
            sys.exit(2)
        account_id = steam_user["AccountID"]

        local_config_path = (steam_path /
                             "userdata" /
                             str(account_id) /
                             "config/localconfig.vdf")

        local_config = try_parsing_file(local_config_path)

        valve = local_config["UserLocalConfigStore"]["Software"]["Valve"]
        return valve["Steam"]["apps"][game_id]["LaunchOptions"]


def try_parsing_file(path):
    try:
        cur_file = open(path, encoding="utf-8")
    except OSError as e:
        raise Exception("Cannot find " + str(path) + "\n" + str(e)) from e
    file_type = str(path).split(".")[-1]
    if file_type in ("vdf", "acf"):
        def _file_load():
            return vdf.load(cur_file)
    elif file_type == "json":
        def _file_load():
            return json.load(cur_file)
    else:
        raise ValueError("File type unknown")
    try:
        parsed = _file_load()
    except ValueError as e:
        raise Exception("Cannot parse " + str(path) + "as " + file_type + " file\n"
                        + str(e)) from e
    return parsed


def generate_random_password():
    ch = string.ascii_letters + string.digits
    password = ''.join(SystemRandom().choice(ch) for _ in range(randint(20, 30)))
    logging.debug("rcon password: " + password)
    return password


def find_free_random_port():
    for _ in range(10):
        cur_port = randint(10240, 65534)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('localhost', cur_port)):
                logging.debug("rcon port: " + str(cur_port))
                return cur_port
        raise ValueError("Could not find empty port for rcon after 10 retries")


class SteamState(Enum):
    NOT_STARTED = 0
    NO_MANGOHUD = 1
    NOT_CONFIGURED = 2
    CONFIGURED = 3


def get_steam_state(mangohud_cfg):
    for proc in psutil.process_iter():
        try:
            processes = proc.name().lower()
        except psutil.AccessDenied as e:
            raise Exception("Could not check if Steam is running: Access Denied\n"
                            + str(e)) from e
        if "steam" in processes:
            proc_env = proc.environ()
            # if "MANGOHUD" not in proc_env:
            if "GAME_DEBUGGER" not in proc_env:
                return SteamState.NO_MANGOHUD
            if "MANGOHUD_CONFIG" not in proc_env:
                return SteamState.NOT_CONFIGURED
            for cfg in mangohud_cfg:
                if cfg not in proc_env["MANGOHUD_CONFIG"].split(","):
                    return SteamState.NOT_CONFIGURED
            return SteamState.CONFIGURED
    return SteamState.NOT_STARTED


def launch_game(game, host_port, rcon_password,
                mangohud_cfg, additional_launch_options=()):
    if platform.system() == "Linux":
        steam_bin = "steam"
    elif platform.system() == "Windows":
        steam_bin = find_steam_dir() / "steam.exe"

    if type(game) is int:
        log_path = "demoknight.log"
        game_list = [steam_bin, "-applaunch", str(game)]
    elif type(game) is str:
        log_path = game + "demoknight.log"
        game_list = [game]

    required_launch_options = [
        "+con_logfile",
        log_path,  # TODO:Doesn't work when game is a path
        "-usercon",
        "-condebug",
        "-conclearlog",
        # "+developer", "1", "+alias", "developer",
        # "+contimes", "0", "+alias", "contimes",
        "+ip", "0.0.0.0", "+alias", "ip",
        "+sv_rcon_whitelist_address", "127.0.0.1",
        "+rcon_password", rcon_password, "+alias", "rcon_password",
        "+hostport", host_port, "+alias", "hostport",
        "+net_start",
        # "+con_timestamp", "1", "+alias", "con_timestamp",
        # "+net_showmsg", "svc_UserMessage",
        # "+alias", "net_showmsg"
    ]
    # create independent process just to make sure
    # https://stackoverflow.com/questions/13243807/popen-waiting-for-child-process-even-when-the-immediate-child-has-terminated/13256908#13256908
    kwargs = {}
    if platform.system() == 'Windows':
        # from msdn [1]
        NEW_PROCESS_GROUP = 0x00000200
        DETACHED_PROCESS = 0x00000008
        kwargs.update(creationflags=DETACHED_PROCESS | NEW_PROCESS_GROUP)
    elif platform.system() == "Linux":
        kwargs.update(start_new_session=True)
    else:
        raise NotImplementedError("Unsupported OS, this tool is only available for "
                                  "Windows and Linux")

    launch_options = (game_list
                      + additional_launch_options
                      + required_launch_options)

    # GAME_DEBUGGER for TF2,L4D2; DEBUGGER for CSGO needs to be changed as to not
    # conflict with Steam's DEBUGGER
    mangohud_env = {"GAME_DEBUGGER": "mangohud",
                    # "DEBUGGER": "mangohud",
                    "MANGOHUD_CONFIG": ",".join(mangohud_cfg)}
    sys_env = os.environ.copy()
    sys_env.update(mangohud_env)
    Popen(launch_options,
          stdin=PIPE,
          stdout=PIPE,
          stderr=PIPE,
          **kwargs,
          env=sys_env)
    # assert not p.poll()
    return


def rcon_command(rcon_password, host_port, command):
    response = ""
    logging.info("Issued rcon command: " + " ".join(command))
    while True:
        try:
            with Client("127.0.0.1",
                        host_port,
                        passwd=rcon_password,
                        # extremely long timeout because demo_timescale messes
                        # with response time
                        timeout=30) as client:
                response = client.run(*command)
        except TimeoutError:
            logging.info("Rcon command timed out, retrying in 1 second.")
            sleep(1)
            continue
        except ConnectionRefusedError:
            # This usually means the game has not finished starting yet, or that it
            # crashed.
            logging.info("Rcon connection refused, retrying in 1 second")
            sleep(1)
            continue
        except ConnectionResetError:
            logging.info("Rcon connection reset, retrying in 1 second")
            sleep(1)
            continue
        except EmptyResponse:
            # Empty responses usually mean the game is currently at a loading screen
            logging.info("Game sent an empty response, retrying in 1 second")
            sleep(1)
            continue
        # Not every command sends a response, so i have to assume its a success
        # if there is no connection or timeout error
        break
    return response


if __name__ == "__main__":
    main()
