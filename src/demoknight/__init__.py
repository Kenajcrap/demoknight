import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from platform import system, platform
from tempfile import gettempdir

import numpy as np
from . import vdf_patch
import vdf
import yaml
from psutil import NoSuchProcess
from steamid import SteamID
import GPUtil

from .test import Test

if system().startswith("Win"):
    import winreg  # pylint: disable=import-error
    from shutil import which

    import win32api
    import win32net
    import win32security


def main():
    argv = sys.argv[1:]

    file_parser = argparse.ArgumentParser(
        allow_abbrev=False, prefix_chars="-", add_help=False
    )

    # JSON config support
    file_parser.add_argument(
        "-j",
        "--job-file",
        help=(
            "Path to yaml configuration file. Supports all launch options except"
            ' "job-file" and "help", as well as a an advanced list of changes for each'
            " test. Options in the file will be overwritten by options passed as"
            " command line options"
        ),
        metavar="PATH",
    )

    file_parser.add_argument(
        "-v",
        "--verbosity",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging verbosity. Default: %(default)s",
    )

    args, rest_argv = file_parser.parse_known_args()

    game_parser = argparse.ArgumentParser(
        allow_abbrev=False, prefix_chars="-", add_help=False, parents=[file_parser]
    )

    numeric_loglevel = getattr(logging, args.verbosity.upper(), None)

    logging.basicConfig(level=numeric_loglevel)

    if args.job_file:
        file_dict = try_parsing_file(args.job_file)

        # Take the list of tests out, we will treat them sepparately
        if file_dict:
            args.tests = file_dict.pop("tests", [])

        # The rest goes in a string to be parsed the same as command line options,
        # unless the options are empty strings
        for k, v in file_dict.items():
            if v not in ("", None):
                rest_argv.insert(0, f"--{k}={str(v)}")

        argv_and_parsed = argv + list(file_dict.keys())
    else:
        argv_and_parsed = argv

    game_parser.add_argument(
        "-g",
        "--gameid",
        required=not [
            x
            for x in (
                "-G",
                "--game-path",
                "-g",
                "--gameid",
                "gameid",
                "game-path",
                "-h",
                "--help",
            )
            if x in argv_and_parsed
        ],
        default=0,
        type=int,
        help=(
            "The gameid used to launch the game through Steam. Takes preference over"
            " 'game-path'. Required if 'game-path' is not used"
        ),
    )

    game_parser.add_argument(
        "-G",
        "--game-path",
        type=Path,
        required=not [
            x
            for x in (
                "-G",
                "--game-path",
                "-g",
                "--gameid",
                "game-path",
                "gameid",
                "-h",
                "--help",
            )
            if x in argv_and_parsed
        ],
        help=(
            "Path to game executable. If gameid is not specified, game will not launch"
            " through Steam. Required if 'gameid' is not used"
        ),
    )

    game_parser.add_argument(
        "-S",
        "--steam-path",
        type=Path,
        default=find_steam_dir(),
        help="Path to the steam folder. Automatically detected if not specified",
    )

    args, rest_argv = game_parser.parse_known_args(args=rest_argv, namespace=args)

    interval_per_gameid = {440: 0.015, 770: 0.015625, 550: 0.03333333, 500: 0.03333333}

    # Find game if gameid is specified
    tick_interval_required = False
    default_tick_interval = 0.015
    if args.gameid:
        if args.game_path:
            logging.warning(
                "--gameid specified. --game-path will be ignored and the game directory"
                " will be derived from libraryfolders.vdf"
            )
        if args.gameid in interval_per_gameid:
            default_tick_interval = interval_per_gameid[args.gameid]
            logging.info(
                f"Default --tick-interval for {args.gameid} found:"
                f" {interval_per_gameid[args.gameid]}"
            )
        else:
            tick_interval_required = True
            logging.warning(
                f"Default tick-interval not found for gameid {args.gameid}, --tickrate"
                " or --tick-interval will be required"
            )
        args.game_path = find_game_dir(args.steam_path, args.gameid)
    else:
        # Or, at least find gameid from gameinfo so we know the default tickrate for some of
        # the games
        test_gameid = 0
        if any("game-path" in d for d in getattr(args, "tests", [])):
            for v in (d for d in args.tests if "game-path" in d):
                gameid = find_id_from_game_path(v.get("game-path"))
                if test_gameid and test_gameid != gameid:
                    tick_interval_required = True
                    logging.warning(
                        "Conflicting gameids found in test game-paths, --tickrate or"
                        " --tick-interval will be required"
                    )
                    break
                else:
                    test_gameid = gameid
            else:
                if test_gameid in interval_per_gameid.keys():
                    default_tick_interval = interval_per_gameid[test_gameid]
                else:
                    tick_interval_required = True
                    logging.warning(
                        "Couldn't find default --tick_interval for gameid"
                        f" {test_gameid}, --tickrate or --tick_interval will be"
                        " required"
                    )
        elif not tick_interval_required and args.game_path:
            gameid = find_id_from_game_path(args.game_path)
            if test_gameid and gameid != test_gameid:
                logging.warning(
                    "Conflicting gameids found in between global game-path and test"
                    " game-path, --tickrate or --tick-interval will be required"
                )
                tick_interval_required = True
            else:
                if args.gameid in interval_per_gameid:
                    default_tick_interval = interval_per_gameid[gameid]
                else:
                    tick_interval_required = True
                    logging.warning(
                        f"Couldn't find default --tick_interval for gameid {gameid},"
                        " --tickrate or --tick_interval will be required"
                    )

    parser = argparse.ArgumentParser(
        allow_abbrev=False, prefix_chars="-", parents=[game_parser]
    )

    tkgroup = parser.add_mutually_exclusive_group(required=tick_interval_required)

    tkgroup.add_argument(
        "-T",
        "--tick-interval",
        nargs=1,
        type=float,
        default=default_tick_interval,
        help=(
            "Time interval between ticks of the demo being played, in seconds. Default:"
            " %(default)s"
        ),
    )

    tkgroup.add_argument(
        "-t",
        "--tickrate",
        nargs=1,
        type=lambda x: float((1 / x if 66 <= x <= 67 else 0.015)),
        dest="tick_interval",
        help="Server tickrate of the demo being played. Default: %(default)s",
    )

    parser.add_argument(
        "--comment",
        type=str,
        default="",
        help=(
            "Comment attached to the output file, to be used in data analysis"
            " by other tools"
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
        "-D",
        "--demo-path",
        required=True,
        help=(
            "Path to the demo file, starting from the game's 'mod' directory (same as"
            " the 'playdemo' console command in-game). Required"
        ),
    )

    if system().startswith("Win"):
        parser.add_argument(
            "--presentmon-path",
            type=Path,
            required=not bool(which("presentmon")),
            help="(Windows only) Path to PresentMon executable. Default: 'presentmon'",
        )

    parser.add_argument(
        "-l",
        "--launch-options",
        nargs=1,
        default=(),
        action=SplitSimple,
        help=(
            "Additional launch options to use for every test, added to the ones gotten"
            " from steam if using --gameid. If using --game-path, don't forget required"
            " launch options like '-game'. For multiple arguments, use the form"
            " '-l=\"-option1 -option2\"')"
        ),
    )

    parser.add_argument(
        "-n",
        "--passes",
        default=5,
        type=int,
        help="Number of passes done for each test. Default: %(default)s",
    )

    parser.add_argument(
        "-L",
        "--loops",
        default=1,
        type=int,
        help=(
            "Number of times to run the benchmark. If set to more than 1,"
            " the benchmark will start from the first test again after finishing"
            " the last one. Use 0 to loop indefinitely. Default: %(default)s"
        ),
    )

    parser.add_argument(
        "-k",
        "--keep-first-pass",
        default=False,
        action=StoreTrueFalseAction,
        nargs="?",
        const=True,
        help=(
            "Keep first pass of each test. Discarting the first pass is needed if the"
            " demo section used for benchmark is the very start, since performance"
            " there is not representative. Default: %(default)s"
        ),
    )

    parser.add_argument(
        "-s",
        "--start-tick",
        default=(66 * 2) + 55,
        type=int,
        help=(
            "Start of the benchmark section of the demo in ticks. Default: %(default)s"
        ),
    )

    # Mangohud doesn't start capturing at the start as soon as we tell it to.
    # Therefore we gotta start a second earlier to be safe
    # Fast fowarding causes particles and props to behave unlike they do
    # normally and cause lag once we resume normal playback. We gotta let those
    # settle down before we can get good data.

    # TODO: Check if tick is at the very start of a demo and refrain from
    # fast-fowarding instead.

    parser.add_argument(
        "--start-buffer",
        default=2,
        type=float,
        help=(
            "After fast-fowarding a demo, particles and physics objects can take longer"
            " than they should to de-spawn. This safety buffer ensures that they do"
            " before starting the benchmark. In seconds. Default: %(default)s"
        ),
    )

    parser.add_argument(
        "-d",
        "--duration",
        default=20.0,
        type=float,
        help="Benchmark duration in seconds. Default: %(default)s.",
    )

    parser.add_argument(
        "-o",
        "--output-file",
        type=Path,
        default=Path(f"summary_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"),
        help="path for the generated summary file. Default: %(default)s",
    )

    parser.add_argument(
        "-b",
        "--no-baseline",
        default=False,
        action=StoreTrueFalseAction,
        nargs="?",
        const=True,
        help=(
            "Whether or not to capture a baseline test without applying changes."
            " Default: %(default)s"
        ),
    )

    parser.add_argument(
        "tests",
        action=SplitArgs,
        nargs="*",
        help=(
            "Space separated inline test list instead of reading from yaml (one item"
            " per test). Options starting with '+' will be treated as cvars and"
            " executed in the main menu. Options starting with '-' will be treated as"
            " launch options. Use ' -- ' to separate this from the named options if"
            " launch options are used"
        ),
    )
    # Overwrite config file with command line options
    parser.parse_args(args=rest_argv, namespace=args)

    # Presentmon group required
    if system().startswith("Win"):
        check_local_group()

    # Logging verbosity
    if not isinstance(numeric_loglevel, int):
        raise ValueError("Invalid log level: %s" % args.verbosity)

    # Include a job at the top of the list for baseline if required
    if not args.no_baseline:
        if not args.tests:
            args.tests = []
        args.tests.insert(0, {"name": "baseline", "changes": {}})

    # Check for duplicate tests and generate test names and changes if empty
    names = [n["name"] for n in args.tests if n.get("name") is not None]
    if len(names) != len(set(names)):
        raise ValueError("Multiple tests with the same name")

    for t in args.tests:
        if t.get("changes"):
            if not t.get("name"):
                concat_changes = " ".join(
                    [
                        " ".join([i for i in x if i is not None])
                        for x in t["changes"].values()
                    ]
                )
                if concat_changes == " ":
                    concat_changes = "baseline"

                timeout = 0
                while timeout < 50:
                    if concat_changes in names:
                        concat_changes = f"{concat_changes}_{timeout}"
                    else:
                        names.append(concat_changes)
                        t["name"] = concat_changes
                        break
                    timeout = +1
        else:
            t["changes"] = {}
        if t.get("game-path"):
            t["game-path"] = Path(t["game-path"])

    # Create log folder if it doesn't exist

    for n in [i["name"] for i in args.tests]:
        (args.raw_path.absolute() / args.output_file.name / n).mkdir(
            parents=True, exist_ok=True
        )

    # Main testing loop

    # Estimate how much time the job will take and warn the user if more than 1 hour
    # TODO: Improve ETA with real life data
    eta = (
        (
            (
                +3  # load demo
                + (args.start_buffer)  # delay
                + args.duration  # demo playback
                + (args.start_tick * (args.tick_interval or 0.015) / 20)  # fastfoward
            )
            * (args.passes)
            * 1.1
        )
        + 10  # Load game
    ) * len(
        args.tests
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

    args.system = {"CPU": get_cpu_name(), "GPU": get_gpu_name(), "OS": platform()}
    loops = 0
    while loops + 1 != args.loops:
        for test in tests:
            success = False
            while not success:
                print(f"Starting test {test.name}")
                try:
                    test.capture(args)
                except NoSuchProcess:
                    logging.error(
                        "The game seems to have crashed, retrying entire test"
                    )
                    # TODO: If we later allow the tests to run continuously, we need to
                    # handle the clearing of results better
                    test.results.clear()
                    continue
                except KeyboardInterrupt:
                    logging.warning(
                        "KeyboardInterrupt received. Some tests will probably end up with more passes than others."
                    )
                    exit(0)
                args.tests[test.index]["results"] = test.results
                print(f"Finished test {test.name}")
                success = True
                with open(
                    f"{args.output_file.absolute()}.json",
                    "w",
                    newline="",
                    encoding="utf-8",
                ) as outfile:
                    json.dump(args.__dict__, outfile, default=str)
                # test.watchdog.join()

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
            if i[0] in ["-", "+"]:
                tst = self._make_test(last_item)
                tests.append({"changes": tst, "name": " ".join(last_item).lstrip("+")})
                last_item = [i]
            else:
                last_item.append(i)
        else:
            if values:
                tst = self._make_test(last_item)
                tests.append({"changes": tst, "name": " ".join(last_item).lstrip("+")})
        if values:
            setattr(namespace, self.dest, tests)

    def _make_test(self, test):
        prefix = test[0][0] or None
        if prefix == "+":
            return {"cvars": (" ".join(test)[1:],)}
        elif prefix == "-":
            return {"launch_options": (f"-{' '.join(test)[1:]}",)}


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


class SplitSimple(argparse.Action):
    def __call__(self, _, namespace, values, option_string=None):
        if values:
            setattr(namespace, self.dest, tuple(values[0].split(" ")))


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

    for i in game_dir.iterdir():
        if (
            i.is_file()
            and os.access(i, os.X_OK)
            and i.suffix not in (".txt", ".sh", ".bat")
        ):
            return i


def find_id_from_game_path(game_path):
    try:
        gameinfo_path = tuple(Path(game_path).parent.glob("./*/gameinfo.txt"))[0]
    except IndexError as e:
        raise FileNotFoundError(
            f'gameinfo.txt not found in the game_path "{game_path.parent}".\n{e}'
        )
    try:
        gameid = vdf.load(open(gameinfo_path), mapper=vdf.VDFDict)["GameInfo"][
            "FileSystem"
        ]["SteamAppId"]
    except IndexError as e:
        logging.error(
            f"SteamAppId entry not found in {gameinfo_path}, this game might not be"
            f" compatible with the tool\n{e}"
        )
        gameid = 0
    return int(gameid)


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
                    return yaml.safe_load(cur_file)
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
            None, win32security.ConvertStringSidToSid("S-1-5-32-559")
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
            sys.exit(1)


def get_cpu_name():
    if system() == "Windows":
        return processor()
    elif system() == "Linux":
        with open("/proc/cpuinfo", "r") as f:
            for line in f:
                if "model name" in line:
                    return line.split(":")[1].strip()
    return "Unknown"


def get_gpu_name():
    try:
        gpus = GPUtil.getGPUs()
        if gpus:
            return gpus[0].name
    except:
        pass
    return "Unknown"


def construct_yaml_tuple(self, node):
    seq = self.construct_sequence(node)
    # only make "leaf sequences" into tuples, you can add dict
    # and other types as necessary
    if seq and isinstance(seq[0], (list, tuple, dict)):
        return seq
    return tuple(seq)


yaml.add_constructor(
    "tag:yaml.org,2002:seq", construct_yaml_tuple, Loader=yaml.SafeLoader
)


if __name__ == "__main__":
    main()
