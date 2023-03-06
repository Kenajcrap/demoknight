import argparse
import csv
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from platform import system
from tempfile import gettempdir

import numpy as np
import vdf
import yaml
from psutil import NoSuchProcess
from steamid import SteamID

from .test import Test

if system().startswith("Win"):
    import winreg  # pylint: disable=import-error
    from shutil import which

    import win32api
    import win32net
    import win32security


def main():
    argv = sys.argv[1:]
    config_parser = argparse.ArgumentParser(
        description=(
            "A tool that operates mangohud together with rcon to automatically generate"
            " comparative benchmark results for different game setups"
        ),
        prog="DemoKnight",
        prefix_chars="-",
        add_help=False,
    )

    # JSON config support
    config_parser.add_argument(
        "-j",
        "--job-file",
        help=(
            "Path to json file containing config parameters and a list of parameters to"
            " be tested. Currently supports cvars and launch options to be applied"
        ),
    )
    args, rest_argv = config_parser.parse_known_args()
    if args.job_file:
        file_dict = try_parsing_file(args.job_file)

        # Take the list of tests out, we will treat them sepparately
        if file_dict and "tests" in file_dict:
            args.tests = file_dict.pop("tests", [])

        # The rest goes in a string to be parsed the same as command line options,
        # unless the options are empty strings
        for k, v in file_dict.items():
            if v not in ("", None):
                rest_argv.insert(0, str(v))
                rest_argv.insert(0, f"--{k}")

        argv_and_parsed = argv + list(file_dict.keys())
    else:
        argv_and_parsed = argv

    parser = argparse.ArgumentParser(
        parents=[config_parser], allow_abbrev=False, prefix_chars="-"
    )

    parser.add_argument(
        "-g",
        "--gameid",
        required=not [
            x
            for x in ("-G", "--game-path", "gameid", "game-path")
            if x in argv_and_parsed
        ],
        default=0,
        type=int,
        help=(
            "Overrides the gameid used to launch the game, game will be run through"
            " Steam."
        ),
    )

    parser.add_argument(
        "-G",
        "--game-path",
        type=Path,
        required=not [
            x for x in ("-g", "--gameid", "game-path", "gameid") if x in argv_and_parsed
        ],
        help=(
            "Overrides the path to the game's binary file or starting script, game will"
            " not be run through Steam."
        ),
    )

    parser.add_argument(
        "-l",
        "--launch-options",
        default=[],
        type=list,
        nargs="+",
        action="append",
        help=(
            "Additional launch options to use for every test, added to the ones gotten"
            " from steam if using --gameid. If using --gamepath, don't forget required"
            " launch options like '-game'"
        ),
    )

    parser.add_argument(
        "--raw-path",
        type=Path,
        default=Path(gettempdir()) / "demoknight",
        help=(
            "Path to the mangohud/presentmon log files. Defaults to the temporary"
            " folder of your OS."
        ),
    )

    parser.add_argument(
        "-S",
        "--steam-path",
        type=Path,
        default=find_steam_dir(),
        help="Path to the steam folder, can be detected automatically",
    )

    parser.add_argument(
        "-n",
        "--passes",
        default=5,
        type=int,
        help="Overrides the number of passes. Default: 5",
    )

    parser.add_argument(
        "-k",
        "--keep-first-pass",
        default=False,
        action=StoreTrueFalseAction,
        nargs="?",
        const=True,
        help=(
            "Keep first pass. Discarting the first pass is needed if the demo section"
            " used for benchmark is the very start, since performance there is not"
            " representative."
        ),
    )

    parser.add_argument(
        "-s",
        "--start-tick",
        default=(66 * 2) + 20,
        type=int,
        help="Start of the benchmark section of the demo in ticks.",
    )

    parser.add_argument(
        "-d",
        "--duration",
        default=20.0,
        type=float,
        help="Benchmark duration in seconds. Default: 20.",
    )

    parser.add_argument(
        "-t",
        "--tickrate",
        default=66.6,
        type=float,
        help="Server tickrate of the demo being played. Default: 66.6",
    )

    parser.add_argument(
        "-D",
        "--demo-path",
        required=True,
        help=(
            "Path to the demo file, starting from the game's 'mod' directory (same as"
            " the 'playdemo' console command in-game)."
        ),
    )

    parser.add_argument(
        "-b",
        "--no-baseline",
        default=False,
        action=StoreTrueFalseAction,
        nargs="?",
        const=True,
        help="Whether or not to capture a baseline test without applying changes",
    )

    parser.add_argument(
        "-o",
        "--output-file",
        type=Path,
        default=Path(f"summary_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"),
        help="Summary file. Default: current date and time",
    )

    parser.add_argument(
        "-v",
        "--verbosity",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging verbosity. Default: WARNING",
    )

    parser.add_argument(
        "--presentmon-path",
        type=Path,
        required=not bool(which("presentmon")),
        help="Path to PresentMon executable. Default: 'presentmon'",
    )

    parser.add_argument(
        "-f",
        "--format",
        default="csv",
        choices=["csv", "json"],
        help="Format of the output file. Default: csv",
    )

    parser.add_argument(
        "tests",
        action=SplitArgs,
        nargs="*",
        help=(
            "Space separated inline test list instead of reading from json (one item"
            ' per test). Options starting with "+" will be treated as cvars. Options'
            ' starting with "_" will be treated as launch options.'
        ),
    )
    # Overwrite config file with command line options
    parser.parse_args(args=rest_argv, namespace=args)

    numeric_loglevel = getattr(logging, args.verbosity.upper(), None)

    # Mangohud doesn't start capturing at the start as soon as we tell it to.
    # Therefore we gotta start a second earlier to be safe
    # Fast fowarding causes particles and props to behave unlike they do
    # normally and cause lag once we resume normal playback. We gotta let those
    # settle down before we can get good data.

    # TODO: Check if tick is at the very start of a demo and refrain from
    # fast-fowarding instead.
    start_delay = 2

    if system().startswith("Win"):
        check_local_group()

    if not isinstance(numeric_loglevel, int):
        raise ValueError("Invalid log level: %s" % args.verbosity)
    logging.basicConfig(level=numeric_loglevel)

    # Create log folder if it doesn't exist
    try:
        args.raw_path.absolute().mkdir()
    except FileExistsError:
        pass

    # Find game if gameid is specified
    if args.gameid:
        args.game_path = find_game_dir(args.steam_path, args.gameid)

    # Include a job at the top of the list for baseline if required
    if not args.no_baseline:
        args.tests.insert(0, {"changes": {}})

    # Main testing loop

    # Estimate how much time the job will take and warn the user if more than 1 hour
    # TODO: Improve ETA with real life data
    eta = (
        (
            10  # load game
            + 5  # load demo
            + (start_delay)  # delay
            + args.duration  # demo playback
            + (args.start_tick / (args.tickrate or 66) / 20)
        )  # fastfoward
        * (args.passes)
        * 1.2
        * len(args.tests)
    )  # passes
    logging.info(f"ETA: {eta} seconds")
    if eta > 3600:
        logging.warning(
            f"This job may take more than {str(round((eta/60/60), 2))} hours to"
            " complete. Consider breaking it up into multiple jobs to avoid having to"
            " redo due to crashes or other problems"
        )

    tests = []
    for i, test in enumerate(args.tests):
        tests.append(Test(args, i))

    for test in tests:
        success = False
        while not success:
            try:
                test.capture(args, start_delay)
            except NoSuchProcess:
                logging.error("The game seems to have crashed, retrying entire test")
                # TODO: If we later allow the tests to run continuously, we need to
                # handle the clearing of results better
                test.results.clear()
                continue
            print(f"Finished test {test.name}")
            success = True
            # test.watchdog.join()

    # Import mangohud/presentmon data and set some constants
    if system().startswith("Win"):
        usecols = (7, 9)
        skiprows = 1
        one_second = 1000000000
    elif system().startswith("Linux"):
        usecols = (1, 11)
        skiprows = 3
        one_second = 1000000000
    summary = []
    for test in tests:
        entry = {"name": test.name, "average": [], "variance": []}
        for i, res in enumerate(test.results):
            if i == 0 and not args.keep_first_pass:
                continue
            arr = np.loadtxt(res, delimiter=",", usecols=usecols, skiprows=skiprows)
            # We actually start capturing 2 seconds before we need to, so get
            # rid of those rows
            arr[:, 1] -= start_delay * one_second
            arr = arr[arr[..., 1] >= 0]
            entry["average"].append(np.average(arr[:, 0], axis=0))
            entry["variance"].append(np.var(arr[:, 0], axis=0))

            summary.append(entry)
    with open(f"{args.output_file}.{args.format}", "w", encoding="utf-8") as outfile:
        if args.format == "json":
            json.dump(summary, outfile)
        elif args.format == "csv":
            writer = csv.DictWriter(outfile, fieldnames=["name", "average", "variance"])
            for test in summary:
                test["name"] = [test["name"]] * len(test["average"])
                v2 = [dict(zip(test, t)) for t in zip(*test.values())]
                writer.writeheader()
                writer.writerows(v2)
    print("done")


# TODO: Clean this mess of a class
class SplitArgs(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        tests = []
        last_item = []
        for i in values:
            if not last_item:
                last_item.append(i)
                continue
            if i[0] in ["_", "+"]:
                tests.append({"changes": self._make_test(last_item)})
                last_item = [i]
            else:
                last_item.append(i)
        else:
            if values:
                tests.append({"changes": self._make_test(last_item)})
        if values:
            setattr(namespace, self.dest, tests)

    def _make_test(self, test):
        prefix = test[0][0] or None
        if prefix == "+":
            return {"cvar": [" ".join(test)[1:]]}
        elif prefix == "_":
            return {"launch_options": [f"-{' '.join(test)[1:]}"]}


class StoreTrueFalseAction(argparse.Action):
    def __init__(
        self,
        option_strings,
        dest,
        const=True,
        default=None,
        required=False,
        help=None,
        metavar=None,
        nargs=None,
    ):
        super().__init__(
            option_strings=option_strings,
            dest=dest,
            nargs=nargs,
            const=const,
            default=default,
            required=required,
            help=help,
            metavar=metavar,
        )

    def __call__(self, parser, namespace, values, option_string=None):
        if isinstance(values, bool):
            setattr(namespace, self.dest, values)
        elif values.lower() in ("true", "t", "yes", "y", "1"):
            setattr(namespace, self.dest, True)
        elif values.lower() in ("false", "f", "no", "n", "0"):
            setattr(namespace, self.dest, False)
        else:
            raise argparse.ArgumentError(self, f"Invalid boolean value: {values}")


def find_steam_dir():
    if system().startswith("Linux"):
        steam_dir = Path("~/.steam/steam").expanduser()
        if not Path(steam_dir).expanduser().exists():
            raise FileNotFoundError(
                "Cannot find '~/.steam/steam', ensure Steam is installed or overwrite"
                " with -S"
            )
    elif system().startswith("Win"):
        reg_path = "SOFTWARE\\Valve\\Steam\\ActiveProcess"
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path)
        except OSError as e:
            raise Exception(
                "Cannot find required registry key, ensure Steam is installed for the"
                f" current user or overwrite with -s\n{e}"
            ) from e
        try:
            steam_dll_reg = winreg.QueryValueEx(key, "SteamClientDLL")
            steam_dir = Path(steam_dll_reg[0].rsplit("\\", maxsplit=1)[0])
        except OSError as e:
            raise Exception(
                "Cannot find path to Steam installation, ensure Steam is installed or"
                f" overwrite with -s\n{e}"
            ) from e
    else:
        raise NotImplementedError(
            "Unsupported OS, this tool is only available for Windows and Linux"
        )
    return steam_dir


def find_game_dir(steam_dir, gameid):
    libraries = try_parsing_file(steam_dir / "steamapps/libraryfolders.vdf")

    for k, v in libraries["libraryfolders"].items():
        if not k.isnumeric():
            continue
        elif str(gameid) in v["apps"].keys():
            game_library_dir = Path(v["path"])
            break
    else:
        raise FileNotFoundError(
            "Cannot find path to steam library containing the game, ensure the game is"
            " installed or overwrite with --game_path"
        )

    appmanifest_file_path = game_library_dir / "steamapps" / f"appmanifest_{gameid}.acf"

    appmanifest = try_parsing_file(appmanifest_file_path)

    game_dir = (
        game_library_dir
        / "steamapps"
        / "common"
        / appmanifest["AppState"]["installdir"]
    )

    return game_dir


# Currently unused function, maybe useful later
def get_user_launch_options(steam_path, gameid):
    if system().startswith("Win"):
        # TODO
        return ""
    # Steam running + most recent profile found most likely means he is logged
    # in on this account right now
    elif system().startswith("Linux"):
        loginusers = try_parsing_file(steam_path / "config" / "loginusers.vdf")
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

        local_config = try_parsing_file(local_config_path)

        valve = local_config["UserLocalConfigStore"]["Software"]["Valve"]
        return valve["Steam"]["apps"][gameid]["LaunchOptions"]


def try_parsing_file(path):
    file_type = str(path).split(".")[-1]
    if file_type in ("vdf", "acf"):

        def _file_load(path):
            try:
                with open(path, encoding="utf-8") as cur_file:
                    return vdf.load(cur_file)
            except OSError as e:
                raise Exception(f"Cannot find {path} \n{e}") from e

    elif file_type == "json":

        def _file_load(path):
            try:
                with open(path, encoding="utf-8") as cur_file:
                    no_comments = cur_file.read()
                    no_comments = re.sub(
                        r"//.*?\n|/\*.*?\*/", "", no_comments, flags=re.DOTALL
                    )
                    return json.loads(no_comments)
            except OSError as e:
                raise Exception(f"Cannot find {path} \n{e}") from e

    elif file_type == "yaml":

        def _file_load(path):
            try:
                with open(path, encoding="utf-8") as cur_file:
                    return yaml.load(cur_file, Loader=yaml.FullLoader)
            except OSError as e:
                raise Exception(f"Cannot find {path} \n{e}") from e

    else:
        raise ValueError("File type unknown")

    try:
        parsed = _file_load(path)
    except ValueError as e:
        raise Exception(f"Cannot parse {path} as {file_type} file\n{e}") from e
    return parsed


if system().startswith("Win"):

    def check_local_group():
        group_name = win32security.LookupAccountSid(
            None, win32security.ConvertStringSidToSid("S-1-5-32-558")
        )[0]
        member_name = win32api.GetUserName()
        member_sid = win32security.LookupAccountName(None, win32api.GetUserName())[0]
        members = win32net.NetLocalGroupGetMembers(None, group_name, 2)[0]
        for member in members:
            if member["sid"] == member_sid:
                break
        else:
            print(
                f"The user {member_name} isn't part of the local group 'Performance"
                " Log Users'.\nHow to fix:"
                " https://github.com/GameTechDev/PresentMon#user-access-denied"
            )


if __name__ == "__main__":
    main()
