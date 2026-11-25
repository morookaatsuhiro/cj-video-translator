from pathlib import Path


def report_run(run: Path) -> Path:
    from cjvmt.demo import export_comparison

    return export_comparison(run)
