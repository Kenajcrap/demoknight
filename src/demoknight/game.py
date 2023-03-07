import logging
import multiprocessing as mp
import os
import queue
import re
import socket
import string
import threading
from enum import Enum
from platform import system
from random import SystemRandom, randint
from time import sleep, time

import psutil
from rcon.exceptions import EmptyResponse
from rcon.source import Client
from watchfiles import watch


class GameState(Enum):
    DEFAULT = -1
    NOT_RUNNING = 0
    LOADING = 1
    RUNNING = 2


class Game(psutil.Popen):
    """
    Launches game be it through steam or directly, provides convenience functions for
    some rcon commands that are finicky and keeps track of game state in a very
    rudimentary way.
    """

    required_launch_options = [
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
    ]

    def __init__(self, gameid=0, game_path=None, steam_path=None, l_opts=[], **kwargs):
        self.password = self._rand_pass()
        self.port = self._free_port()
        self.quitted = False
        self.last_position = 0
        self.state = mp.Value("i", GameState.DEFAULT.value)
        self.not_capturing = threading.Event()
        self.not_capturing.set()

        all_launch_options = (
            l_opts
            + Game.required_launch_options
            + [
                "+rcon_password",
                self.password,
                "+alias",
                "rcon_password",
                "+hostport",
                str(self.port),
                "+alias",
                "hostport",
                "+net_start",
            ]
        )

        if gameid:
            if system().startswith("Win"):
                try:
                    steam_bin = steam_path / "steam.exe"
                except TypeError as e:
                    raise Exception("On Windows you need to specify steam_path") from e
            elif system().startswith("Linux"):
                steam_bin = "steam"

            args = [str(steam_bin), "-applaunch", str(gameid)] + all_launch_options

        else:
            args = [game_path] + all_launch_options

        # Check if steam is setup correctly, only start job if it is not running or
        # if it has the correct mangohud config already
        if gameid:
            logging.info("Checking if steam has the right enviroment variables set")
            steam_state = self._steam_state(**kwargs)
            logging.debug(f"Steam State: {steam_state.value}")

            if steam_state in (SteamState.NOT_STARTED, SteamState.CONFIGURED):
                print("Starting game")
                print("The game must be in focus to be benchmarked properly")
            elif steam_state == SteamState.NO_MANGOHUD:
                raise OSError(
                    "Steam is running but does not have the right GAME_DEBUGGER, please"
                    " close Steam so it can be opened with the correct launch options"
                )

            # Due to a vulnerability related to using the steam protocol through a
            # browser, steam has disabled the ability to use %command% in launch
            # options using the 'steam' command

            # So to launch the game with mangohud, it's easier if we just launch
            # steam with GAME_DEBUGGER set. Another option would be to append the
            # vtf file that handles launch options inside ~/.steam.
            elif steam_state == SteamState.NOT_CONFIGURED:
                raise OSError(
                    "Steam is running but does not have the right MANGOHUD_CONFIG,"
                    " please close Steam so it can be opened with the correct launch"
                    " options"
                )
            else:
                raise OSError("unknown steam_state")

        # TODO: Handle ProcessAlreadyRunning
        if self._find_game_proc(game_path):
            raise Exception("Game is already running, close it and try again")

        # Clear log file before each run given we are spamming it so much
        logs = list(game_path.parent.glob("./*/demoknight.log"))
        if len(logs) > 1:
            raise FileNotFoundError("More than one demoknight.log file was found")
        elif logs:
            self.log_path = logs[0]
            try:
                logging.info(f"Removing {self.log_path}")
                os.remove(self.log_path)
            except FileNotFoundError:
                logging.info(f"{self.log_path} doesn't exists, continuig.")
        else:
            self.log_path = None

        super().__init__(args, **kwargs)

        pid = None
        timeout = 0
        while timeout < 60:
            logging.debug("Waiting for game process to launch")
            pid = self._find_game_proc(game_path)
            if pid is None:
                sleep(1)
                timeout += 1
            else:
                break
        else:
            raise TimeoutError("Could not find game process after 60 seconds")

        # Trick psutil into tracking the game instead of the "steam -applaunch" process
        self._init(pid, _ignore_nsp=True)

        self.watchdog_exceptions = queue.Queue()

        self.watchdog = threading.Thread(target=self.update_state, daemon=True)
        self.watchdog.start()

        # Wait for the game to finish loading
        while not self.state.value == GameState.RUNNING.value:
            if not self.watchdog_exceptions.empty():
                raise self.watchdog_exceptions.get()
            sleep(1)

        # Get log if I don't have it already
        while not self.log_path:
            # Sometimes self.state becomes RUNNING too quickly on windows, meaning there
            # isn't a log file yet
            # TODO: Find a better solution
            try:
                self.log_path = list(game_path.parent.glob("./*/demoknight.log"))[0]
            except IndexError:
                pass

    def update_state(self):
        last_not_running = 0
        last_disk_sleep = time()
        while not self.state.value == GameState.NOT_RUNNING.value:
            sleep(0.5)
            # Turn off watchdog while capturing data to reduce influences
            self.not_capturing.wait()

            if self.is_running():
                last_not_running = 0
                logging.debug(f"Process Status: {self.status()}")

                # Loading
                if self.status() in (psutil.STATUS_DISK_SLEEP, psutil.STATUS_SLEEPING):
                    last_disk_sleep = time()
                    self.state.value = GameState.LOADING.value
                    continue
                if time() - last_disk_sleep < 5:
                    self.state.value = GameState.LOADING.value
                    continue

                # Running
                self.state.value = GameState.RUNNING.value
            else:
                # Not running
                last_not_running = last_not_running or time()
                if last_not_running and (time() - last_not_running > 5):
                    self.state.value = GameState.NOT_RUNNING.value
                    continue

            # TODO: Include other states like fastfowarding and Unresponsive
        else:
            if not self.quitted:
                self.watchdog_exceptions.put(psutil.NoSuchProcess(self.pid))

    def rcon(self, command: str):
        if not self.watchdog_exceptions.empty():
            raise self.watchdog_exceptions.get()
        response = ""
        logging.info(f"Issued rcon command: {command}")
        while True:
            try:
                with Client(
                    "127.0.0.1",
                    self.port,
                    passwd=self.password,
                    # extremely long timeout because demo_timescale messes
                    # with response time
                    timeout=30,
                ) as client:
                    response = client.run(command)
            except TimeoutError:
                logging.info("Rcon command timed out, retrying in 1 second.")
                sleep(1)
                continue
            except ConnectionRefusedError:
                # This usually means the game has not finished starting yet, or that it
                # crashed.
                if not self.watchdog_exceptions.empty():
                    raise self.watchdog_exceptions.get()
                logging.info("Rcon connection refused, retrying in 1 second")
                sleep(1)
                continue
            except ConnectionResetError:
                logging.info("Rcon connection reset, retrying in 1 second")
                sleep(1)
                continue
            except EmptyResponse:
                # Empty responses usually mean the game is currently at a loading screen
                # loading screens are tracked by self.watchdog so this should never
                # happen.
                logging.info("Game sent empty response, aborting test and restarting")
                sleep(1)
                continue
            # Not every command sends a response, so i have to assume its a success
            # if there is no connection or timeout error
            break
        return response

    def playdemo(self, demo):
        if not self.watchdog_exceptions.empty():
            raise self.watchdog_exceptions.get()
        """
        Start demo playback and wait for demo to load
        """
        self.rcon(f"playdemo {demo}; demo_debug 1; demo_timescale 0.05")
        # Wait for the game to finish loading
        while not self.state.value == GameState.RUNNING.value:
            if not self.watchdog_exceptions.empty():
                raise self.watchdog_exceptions.get()
            sleep(1)
        self.rcon("demo_timescale 1")

    def quit(self):
        if not self.watchdog_exceptions.empty():
            raise self.watchdog_exceptions.get()
        """'rcon quit', mark the instance as quitted, and remove the log file"""
        self.quitted = True
        self.rcon("quit")
        self.watchdog.join()
        os.remove(self.log_path)

    def gototick(self, tick: int):
        if not self.watchdog_exceptions.empty():
            raise self.watchdog_exceptions.get()
        """
        rcon demo_gototick and wait for the tick to be reached before returning. If the
        game has ramped to full fastfoward speed, it may overshoot the desired tick. So
        this actually runs demo_gototick twice. Once to approach, and the other to the
        """
        self.rcon("demo_debug 1")
        self.rcon(f"demo_gototick {tick-120} 0 0")
        self._wait_for_console(r"Demo message, tick " + str(tick - 120))
        self.rcon("demo_debug 0")
        self.rcon(f"demo_gototick {tick} 0 0")

    @staticmethod
    def _rand_pass():
        ch = string.ascii_letters + string.digits
        password = "".join(SystemRandom().choice(ch) for _ in range(randint(20, 30)))
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

    def _wait_for_console(self, regex_pattern):
        # print(steamdir)
        logpat = re.compile(regex_pattern)
        for _ in watch(self.log_path, force_polling=system().startswith("Win")):
            if self.last_position > os.path.getsize(self.log_path):
                self.last_position = 0
            with open(self.log_path) as f:
                f.seek(self.last_position)
                loglines = f.readlines()
                self.last_position = f.tell()
                groups = (logpat.search(line.strip()) for line in loglines)
                for g in groups:
                    if g:
                        return self.last_position

    @staticmethod
    def _steam_state(env=[], **kwargs):
        for proc in psutil.process_iter():
            try:
                processes = proc.name().lower()
            except psutil.AccessDenied as e:
                raise Exception(
                    f"Could not check if Steam is running: Access Denied\n{e}"
                ) from e
            if "steam" in processes:
                if system().startswith("Linux"):
                    proc_env = proc.environ()
                    # if "MANGOHUD" not in proc_env:
                    if "GAME_DEBUGGER" not in proc_env:
                        return SteamState.NO_MANGOHUD
                    if "MANGOHUD_CONFIG" not in proc_env:
                        return SteamState.NOT_CONFIGURED
                    for cfg in env["MANGOHUD_CONFIG"].split(","):
                        if cfg not in proc_env["MANGOHUD_CONFIG"].split(","):
                            return SteamState.NOT_CONFIGURED
                return SteamState.CONFIGURED
        return SteamState.NOT_STARTED

    @staticmethod
    def _find_game_proc(game_path):
        pid = None
        for p in psutil.process_iter(["pid", "cmdline", "name"]):
            if p.info["cmdline"] and p.info["cmdline"][0].startswith(
                str(game_path.absolute())
            ):
                pid = p.info["pid"]
                logging.info(f"Process found: {p.info['name']}")
        return pid


class SteamState(Enum):
    NOT_STARTED = 0
    NO_MANGOHUD = 1
    NOT_CONFIGURED = 2
    CONFIGURED = 3
