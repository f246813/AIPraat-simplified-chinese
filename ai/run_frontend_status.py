if __name__ == '__main__':
    import sys
    from pathlib import Path
    try:
        from praat_ai.frontend_status import main, report_menu_error
        raise SystemExit(main())
    except Exception as error:
        output = Path(__file__).parent / 'runtime' / 'frontend-menu.json'
        if '--output' in sys.argv:
            index = sys.argv.index('--output') + 1
            if index < len(sys.argv):
                output = Path(sys.argv[index])
        if 'report_menu_error' in globals():
            report_menu_error(error, output)
            raise
        try:
            runtime = output.parent
            runtime.mkdir(parents=True, exist_ok=True)
            (runtime / 'frontend-menu-error.txt').write_text(
                type(error).__name__ + ': ' + str(error), encoding='utf8')
        except OSError:
            pass
        raise
