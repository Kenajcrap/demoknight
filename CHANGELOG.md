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
