import logging
from argparse import Namespace
from datetime import datetime
from os import environ
from platform import system
from time import perf_counter, sleep
from tempfile import gettempdir
from pathlib import Path

import numpy as np
from psutil import Popen, subprocess

from .game import Game

if system().startswith("Linux"):
    import control


class Test:
    """
    Takes care of controlling the game and capture software, and applying changes
    """

    required_mangohud_conf = ("no_display=1", "log_interval=0", "control=mangohud")

    required_presentmon_conf = ("-terminate_after_timed", "-stop_existing_session")

    game_environ = environ.copy()
    game_environ.update({"GAME_DEBUGGER": "mangohud"})

    def __init__(self, args, index):
        self.name = args.tests[index]["name"]
        self.results = []
        self.index = index

    def capture(self, args):
        # create independent process just to make sure
        # https://stackoverflow.com/questions/13243807/popen-waiting-for-child-process-even-when-the-immediate-child-has-terminated/13256908#13256908
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
            temp_dir = Path(gettempdir()) / "demoknight"
            temp_dir.mkdir(parents=True, exist_ok=True)
            temp_conf_dir = temp_dir / "MangoHud.conf"
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
        # Start game and wait for it to finish loading
        gm = Game(
            gameid=args.gameid,
            game_path=args.tests[self.index].get("game-path") or args.game_path,
            steam_path=args.steam_path,
            l_opts=args.launch_options
            + tuple(i for i in test_launch_options if i is not None),
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
        sleep(10)
