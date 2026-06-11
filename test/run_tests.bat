@echo off
:: run_tests.bat
:: Run all IMERS unit tests from the project root.
:: Usage:
::   run_tests.bat                   -- all tests
::   run_tests.bat classify          -- only test_classify_incident.py
::   run_tests.bat -k "cardiac"      -- filter by keyword

cd /d "%~dp0\.."

set MODULE=%1

if "%MODULE%"=="" (
    echo Running ALL IMERS tests...
    python -m pytest test/ -v --tb=short 2>&1
) else if "%MODULE%"=="classify" (
    python -m pytest test/test_classify_incident.py -v --tb=short
) else if "%MODULE%"=="recommend" (
    python -m pytest test/test_recommend_units.py -v --tb=short
) else if "%MODULE%"=="route" (
    python -m pytest test/test_get_route.py -v --tb=short
) else if "%MODULE%"=="protocol" (
    python -m pytest test/test_protocol_indexer.py -v --tb=short
) else if "%MODULE%"=="tts" (
    python -m pytest test/test_tts_agent.py -v --tb=short
) else if "%MODULE%"=="analysis" (
    python -m pytest test/test_analysis_agent.py -v --tb=short
) else if "%MODULE%"=="pipeline" (
    python -m pytest test/test_pipeline_nodes.py -v --tb=short
) else (
    :: Pass remaining args directly to pytest
    python -m pytest test/ %* -v --tb=short
)
