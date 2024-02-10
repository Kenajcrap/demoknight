# DemoKnight

A tool to help automate repeated demo-based benchmarks of (some) source games leveraging [Mangohud](https://github.com/flightlessmango/MangoHud) or [Presentmon](https://github.com/GameTechDev/PresentMon) for frametime capture and [rcon](https://github.com/conqp/rcon) for in-game control.

## Warning

This is the work of an amateur, not a developer. As such, I'm learning as I go and the quality of code here might not be as good as you see elsewhere. A complete rewrite is being worked on.

## Game compatibility

|  Game  | Compatibility | Notes|
|--------|---------------|------|
| TF2    | Yes           |      |
| CSGO   | Some issues, random crashes  |rename `DEBUGGER` as `GAME_DEBUGGER` in csgo.sh to prevent an env var conflict  |
| Others | Untested      |      |

## Motivation

The source engine is old and can have some unintuitive quirks when it comes to performance compared to other ones. This tool was created to automate more granular testing of in-game parameters, mods and other system tweaks while treating the game as much as a "black box" as possible by not using in-game tools like `timedemo` to gather data.

The automation also allows the user to leverage simple statistical analysis to increase the confidence of results at the cost of longer total test time repeated tests and more attention to the load being applied

## Installation

For Linux users [Mangohud](https://github.com/flightlessmango/MangoHud) needs to be installed and be accessible through `mangohud`.

For Windows users, [PresentMon](https://github.com/GameTechDev/PresentMon) needs to be downloaded and either be accessible through `presentmon` (by adding it to PATH) or pointed to with `--presentmon-path` launch option.

The user running the script needs to be part of the [Performance Log Users](https://github.com/GameTechDev/PresentMon#user-access-denied) user group

After that, clone this repo and install using `pip`

```bash
git clone https://github.com/kenajcrap/demoknight && cd demoknight
pip install .
```

## Usage

A list of options is available doing `demoknight -h`:

```text
usage: demoknight [-h] [-j PATH] [-v {DEBUG,INFO,WARNING,ERROR,CRITICAL}] [-g GAMEID]
                  [-G GAME_PATH] [-S STEAM_PATH] [-T TICK_INTERVAL | -t TICK_INTERVAL]
                  [--comment COMMENT] [--raw-path RAW_PATH] -D DEMO_PATH
                  [-l LAUNCH_OPTIONS] [-n PASSES] [-L LOOPS] [-k [KEEP_FIRST_PASS]]
                  [-s START_TICK] [--start-buffer START_BUFFER] [-d DURATION]
                  [-o OUTPUT_FILE] [-b [NO_BASELINE]]
                  [tests ...]

positional arguments:
  tests                 Space separated inline test list instead of reading from yaml
                        (one item per test). Options starting with '+' will be treated as
                        cvars and executed in the main menu. Options starting with '-'
                        will be treated as launch options. Use ' -- ' to separate this
                        from the named options if launch options are used

options:
  -h, --help            show this help message and exit
  -j PATH, --job-file PATH
                        Path to yaml configuration file. Supports all launch options
                        except "job-file" and "help", as well as a an advanced list of
                        changes for each test. Options in the file will be overwritten by
                        options passed as command line options
  -v {DEBUG,INFO,WARNING,ERROR,CRITICAL}, --verbosity {DEBUG,INFO,WARNING,ERROR,CRITICAL}
                        Logging verbosity. Default: WARNING
  -g GAMEID, --gameid GAMEID
                        The gameid used to launch the game through Steam. Takes
                        preference over 'game-path'. Required if 'game-path' is not used
  -G GAME_PATH, --game-path GAME_PATH
                        Path to game executable. If gameid is not specified, game will
                        not launch through Steam. Required if 'gameid' is not used
  -S STEAM_PATH, --steam-path STEAM_PATH
                        Path to the steam folder. Automatically detected if not specified
  -T TICK_INTERVAL, --tick-interval TICK_INTERVAL
                        Time interval between ticks of the demo being played, in seconds.
                        Default: 0.015
  -t TICK_INTERVAL, --tickrate TICK_INTERVAL
                        Server tickrate of the demo being played. Default: None
  --comment COMMENT     Comment attached to the output file, to be used in data analysis
                        by other tools
  --raw-path RAW_PATH   Path to the mangohud/presentmon log files. Defaults to the
                        temporary folder of your OS.
  -D DEMO_PATH, --demo-path DEMO_PATH
                        Path to the demo file, starting from the game's 'mod' directory
                        (same as the 'playdemo' console command in-game). Required
  -l LAUNCH_OPTIONS, --launch-options LAUNCH_OPTIONS
                        Additional launch options to use for every test, added to the
                        ones gotten from steam if using --gameid. If using --game-path,
                        don't forget required launch options like '-game'. For multiple
                        arguments, use the form '-l="-option1 -option2"')
  -n PASSES, --passes PASSES
                        Number of passes done for each test. Default: 5
  -L LOOPS, --loops LOOPS
                        Number of times to run the benchmark. If set to more than 1, the
                        benchmark will start from the first test again after finishing
                        the last one. Use 0 to loop indefinitely. Default: 1
  -k [KEEP_FIRST_PASS], --keep-first-pass [KEEP_FIRST_PASS]
                        Keep first pass of each test. Discarting the first pass is needed
                        if the demo section used for benchmark is the very start, since
                        performance there is not representative. Default: False
  -s START_TICK, --start-tick START_TICK
                        Start of the benchmark section of the demo in ticks. Default: 187
  --start-buffer START_BUFFER
                        After fast-fowarding a demo, particles and physics objects can
                        take longer than they should to de-spawn. This safety buffer
                        ensures that they do before starting the benchmark. In seconds.
                        Default: 2
  -d DURATION, --duration DURATION
                        Benchmark duration in seconds. Default: 20.0.
  -o OUTPUT_FILE, --output-file OUTPUT_FILE
                        path for the generated summary file. Default:
                        summary_2024-02-10_19-59-34
  -b [NO_BASELINE], --no-baseline [NO_BASELINE]
                        Whether or not to capture a baseline test without applying
                        changes. Default: False
```

Examples:

```text
demoknight --help
demonkight --job_file benchconfigfile.yaml
demoknight -g 440 --job_file benchconfigfile.yaml
demoknight -g 770 --start-tick 35 --duration 20 --tickrate 64 --passes 15 --demo-path demos/benchdemo -- -threads 2 +exec testconfig +r_cheapwaterend 1
demoknight -G /Games/SteamLibrary/steamapps/common/Team\ Fortress\ 2/hl2.sh --launch_options="-steam -game tf -insecure" --k
```

There is also a [template](https://github.com/Kenajcrap/demoknight/blob/main/config_template.yaml) for the `--job_file` file available

## Planned Improvements

In order of expedience

- Integrate visualization options in the tool in a way that is easy for the user to expand on.
- File manipulation as test option: Currently you can apply launch options and cvars, it would be nice to be able to switch files for testing mods.
- Including metadata about the game being tested and the system in the summary output.

## Known Issues

- If the game crashes to main menu instead of to the desktop (like when a demo file is corrupted or not fully compatible) the tool will silently capture invalid and continue
- Currently the tool fails to proceed if the demo file specified is missing.
- The tool does not parse `gameinfo.txt`, therefore, it cannot properly handle games that store the main executable or log files outside the main game folder.

## Data visualization

Data visualization tools are currently not included in demoknight, however, some simple scripts can be found in `scripts` that take in the json output from demoknight or the folder containing the raw files. You will probably need to modify the scripts to use them properly.
