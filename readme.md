# DemoKnight
A tool to help automate repeated demo-based benchmarks of (some) source games leveraging [Mangohud](https://github.com/flightlessmango/MangoHud) or [Presentmon](https://github.com/GameTechDev/PresentMon) for frametime capture and [rcon](https://github.com/conqp/rcon) for in-game control. The tool is currently Linux only but Windows compatibility is planned soon.

### Warning
This is the work of an amateur, not a developer. As such, I'm learning as I go and the quality of code here might not be as good as you see elsewhere. A complete rewrite is being worked on.

## Game compatibility
|  Game  | Compatibility | Notes|
|--------|---------------|------|
| TF2    | Yes           |      |
| CSGO   | Some issues, random crashes  |rename `DEBUGGER` as `GAME_DEBUGGER` in csgo.sh to prevent an env var conflict  |
| Others | Untested      |      |

## Motivation
The source engine is old and can have some unintuitive quirks when it comes to performance compared to other ones. This tool was created to automate more granular testing of in-game parameters, mods and other system tweaks while treating the game as much as a "blackbox" as possible by not using in-game tools like `timedemo` to gather data.

The automation also allows the user to leverage simple statistical analysis to increase the confidence of results at the cost of longer total test time repeated tests and more attention to the load being applied (see [guide]() for some tips)

## Installation
For Linux users [Mangohud](https://github.com/flightlessmango/MangoHud) needs to be installed and be accessible through `mangohud`.
Also, you will need to clone their repo and manually install their python lib:
```bash
git clone https://github.com/flightlessmango/MangoHud && cd MangoHud
pip install control
```

For Windows users, [PresentMon](https://github.com/GameTechDev/PresentMon) needs to be downloaded and either be accessible through `presentmon` (by adding it to PATH) or pointed to with `--presentmon-path` launch option.

After that, clone this repo and install using `pip`
```bash
git clone https://github.com/kenajcrap/demoknight && cd demoknight
pip install .
```
## Usage
Examples:
```
demoknight --help
demoknight -g 440 --job_file benchconfigfile.yaml
demoknight -g 770 --start-tick 35 --duration 20 --tick-rate 64 --passes 15 --demo-path demos/benchdemo --format json _threads 2 +exec testconfig +r_cheapwaterend 1
demoknight -G /Games/SteamLibrary/steamapps/common/Team\ Fortress\ 2/hl2.sh --launch_options "-steam -game tf -insecure" --k
```
A list of options is available doing `demoknight -h`

There is also a [template]() for the `--job_file` file available
## Planned Improvements
In order of expedience

- Complete rewrite: I was learning and researching as I went, which resulted in very messy code. This is very important to be able to accept contributions properly.
- Windows compatibility: PresentMon may be used to allow compatibility on Windows
- File manipulaton as test option: Currently you can apply launch options and cvars, it would be nice to be able to switch files for testing mods.
- Popular statistical indicators like n-th percentile
- Including metadata about the game being tested and the system in the summary output.

## Known Issues
- If the game crashes to main menu instead of to the desktop (like when a demo file is corrupted or not fully compatible) the tool will silently capture invalid and continue
## Data visualisation
Data visualisation tools are currently not included in demoknight, however, some simple scritps can be found in `datavis` that take in the json output from demoknight

