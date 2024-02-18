import logging
import multiprocessing as mp
import os
import queue
import re
import socket
import string
import threading
from pathlib import Path
from enum import Enum
from platform import system
from random import SystemRandom, randint
from time import sleep, time

import psutil

# TODO: Undo monkey patch when pull request is merged: https://github.com/ValvePython/vdf/pull/53
from . import vdf_patch
import vdf
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

    def __init__(
        self, gameid=0, game_path=None, steam_path=None, l_opts=tuple(), **kwargs
    ):
        self.password = l_opts[l_opts.index("+rcon_password") + 1]
        self.port = int(l_opts[l_opts.index("+hostport") + 1])
        self.quitted = 0
        self.last_position = 0
        self.state = mp.Value("i", GameState.DEFAULT.value)
        self.not_capturing = threading.Event()
        self.not_capturing.set()

        if gameid:
            if system().startswith("Win"):
                try:
                    steam_bin = steam_path / "steam.exe"
                except TypeError as e:
                    raise Exception("On Windows you need to specify steam_path") from e
            elif system().startswith("Linux"):
                steam_bin = "steam"

            args = (str(steam_bin), "-applaunch", str(gameid)) + l_opts

        else:
            args = ("mangohud", game_path) + l_opts

        # Check if steam is setup correctly, only start job if it is not running or
        # if it has the correct mangohud config already
        if gameid:
            logging.info("Checking if steam has the right enviroment variables set")
            steam_state = Game._steam_state(**kwargs)
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
        if Game._find_game_proc(game_path):
            raise Exception("Game is already running, close it and try again")

        gameinfo_path = tuple(game_path.parent.glob("./*/gameinfo.txt"))[0]

        # Find log locations
        gameinfo = vdf.load(open(gameinfo_path), mapper=vdf.VDFDict)
        write_paths = []
        for k, v in gameinfo["GameInfo"]["FileSystem"]["SearchPaths"].iteritems():
            if v.startswith("|all_source_engine_paths|"):
                v = game_path.parent.absolute() / Path(
                    v.replace("|all_source_engine_paths|", "")
                )
            elif v.startswith("|gameinfo_path|"):
                v = game_path.parent.absolute() / Path(v.replace("|gameinfo_path|", ""))
            else:
                v = game_path.parent.absolute() / Path(v)
            if any(
                i in k.split("+")
                for i in ("default_write_path", "game_write", "mod_write")
            ):
                if v not in write_paths:
                    write_paths.append(Path(v))

        logs = [file for path in write_paths for file in path.glob("./demoknight.log")]

        # Clear log file before each run given we are spamming it so much
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
            pid = Game._find_game_proc(game_path)
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
                self.log_path = [
                    file
                    for path in write_paths
                    for file in path.glob("./demoknight.log")
                ][0]
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

                # Zombie
                if self.quitted:
                    if time() - self.quitted > 10:
                        logging.warning(
                            "Game took too long to quit, killing process now."
                        )
                        self.kill()

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
                    timeout=5,
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
            except (ConnectionResetError, ConnectionAbortedError):
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
        Start demo playback and wait for demo to load, if the file is not found an
        exception is raised
        """
        self.rcon("demo_debug 1")
        res = self.rcon(f"playdemo {demo}")
        if res.startswith("CDemoFile::Open: couldn't open file "):
            raise FileNotFoundError(
                f"Demo file not found by the game.\nRcon response: {res}"
            )
        self.rcon("demo_timescale 0.01")
        # Wait for the demo to finish loading
        self._wait_for_tick(10)
        self.rcon("demo_timescale 1; demo_debug 0")

    def quit(self):
        if not self.watchdog_exceptions.empty():
            raise self.watchdog_exceptions.get()
        """'rcon quit', mark the instance as quitted, and remove the log file"""
        self.quitted = time()
        self.rcon("quit")
        self.watchdog.join()
        os.remove(self.log_path)

    def gototick(self, tick: int, tick_interval):
        if not self.watchdog_exceptions.empty():
            raise self.watchdog_exceptions.get()
        """
        rcon demo_gototick and wait for the tick to be reached before returning. If the
        game has ramped to full fastfoward speed, it may overshoot the desired tick. So
        this actually runs demo_gototick twice. Once to approach, and the other to the
        """
        # Lower timescale too much and the game becomes unresponsive, lower too little
        # and you can't start respond fast enough, and have to increase the buffer
        # between gototicks and prevent the start of the demo from being used
        self.rcon("demo_debug 1")
        end = 0
        scale = 24
        while scale > 0.05 and tick - end > 10:
            end = round(end + ((tick - end) / 2))
            scale = (tick_interval * (tick - end)) / 2
            if scale > 12:
                self.rcon(f"demo_gototick {end}")
                end = self._wait_for_tick(end)
                continue
            else:
                logging.warning(f"timescale: {scale} end: {end}")
                self.rcon(f"demo_timescale {scale}")
                end = self._wait_for_tick(end)
        if end > tick:
            raise TimeoutError(
                "Log file was not updated fast enough, making the demo go past desired tick"
            )
        logging.warning(f"waiting for: {tick}")
        self.rcon("demo_timescale 0.05")
        self._wait_for_tick(tick)
        self.rcon("demo_debug 0; demo_timescale 1")
        return True

    def _wait_for_tick(self, tick):
        # print(steamdir)
        curr_tick_pat = re.compile(r"[0-9]+(?= dem\_usercmd)")
        demo_stop_pat = re.compile(r"dem\_stop")
        with open(self.log_path) as f:
            for _ in watch(
                self.log_path,
                force_polling=system().startswith("Win"),
                poll_delay_ms=50,
                yield_on_timeout=True,
            ):
                if self.last_position > os.path.getsize(self.log_path):
                    self.last_position = 0
                f.seek(self.last_position)
                loglines = f.readlines()
                self.last_position = f.tell() - 100  # a bit of overlap wont hlurt
                for line in reversed(loglines):
                    # Start from the last one because time only moves foward
                    regm = curr_tick_pat.match(line.strip())
                    if regm:
                        currtick = int(regm.group())
                        if currtick >= tick:
                            return currtick
                    if demo_stop_pat.search(line.strip()):
                        raise RuntimeError(
                            "Demo ended unexpectedly, probably due to log file lagging behind"
                        )
            else:
                raise TimeoutError("Log file took more than 5 seconds to be updated")

    @staticmethod
    def _steam_state(env=(), **kwargs):
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
                    if "MANGOHUD_CONFIGFILE" not in proc_env:
                        return SteamState.NOT_CONFIGURED
                    for cfg in env["MANGOHUD_CONFIGFILE"].split(","):
                        if cfg not in proc_env["MANGOHUD_CONFIGFILE"].split(","):
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
