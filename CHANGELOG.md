## 1.1.0 (2024-02-19)

### Feat

- support more than one test
- update keep-first-pass to discard_passes

### Fix

- format better the graphs, fix typos

## 1.0.0 (2024-02-18)

### BREAKING CHANGE

- --format no longer exists
- --percentiles has been removed

### Feat

- update to new output format
- better legend and notes
- Allow for more than one pass to be discarded
- account for variable sample size in titles
- graph per pass boxplot
- handle unequal sample sizes
- add missing comment field
- include basic hardware and system information

### Fix

- display mangohud so that it captures properly
- kill game process if it lingers in a zombie state
- make sure the new mangohud file is actually new
- fix loops and retry after game crash
- update for new output format
- update some scritps to use the new output format
- shorten rcon password
- Watch out for slow logs
- account for client skipping ticks due to FPS

### Refactor

- clean up old scritps
- update to new output file format
- move launch option crafting from Game to Test
- simplify output file, add loops
- remove percentiles

## 0.1.0 (2023-03-26)

### Feat

- add --tick-interval and --start-buffer

### Fix

- Slow down gototick to account for windows
- Don't require tick_interval if there are no tests
- Use correct types in find_id_from_game_path
- Don't assume args.tests exist before inline tests
- Use timescale to fast-foward unless its too far
- Parse verbosity earlier
- Look for game-path inside test, not game_path
- Tweak playdemo and gototick slightly
- Parse gameinfo.txt for log paths
- Ignore text and script files in find_game_dir
- Monkey patch vdf.parse to allow + and |
- raise exception if demo file not found
- Parse tests one argument at the time

### Refactor

- reorganize arguments, rewrite descriptions
