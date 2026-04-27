@echo off
REM Run the parsidion trigger eval from a SEPARATE terminal (Windows).
REM
REM Usage:
REM   run_trigger_eval.bat
REM
REM Results written to:
REM   %USERPROFILE%\.claude\skills\parsidion\eval_results.json
REM
REM After it finishes, go back to Claude Code and say:
REM   "read the eval results"

set CLAUDECODE=
python "%USERPROFILE%\.claude\skills\parsidion\scripts\run_trigger_eval.py" %*
