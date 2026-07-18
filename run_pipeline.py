"""Prepare and assess a distillation run; deliberately makes no LLM calls."""
import argparse, subprocess, sys
from pathlib import Path
from extract_transcript_text import extract, source_root, DEFAULT_STATE

def main():
    p=argparse.ArgumentParser(); p.add_argument("--project",required=True); p.add_argument("--staging",type=Path,required=True); p.add_argument("--target",type=Path,required=True)
    p.add_argument("--source",type=Path); p.add_argument("--state",type=Path,default=DEFAULT_STATE); p.add_argument("--work",type=Path,default=Path(__file__).parent/"runs")
    a=p.parse_args(); extracts=a.work/a.project/"extracts"; reports=a.work/a.project/"reports"; reports.mkdir(parents=True,exist_ok=True)
    chunks=extract((a.source or source_root())/a.project,extracts,a.state)
    print(f"Chunks awaiting external distillation: {len(chunks)}"); [print(f"  {x}") for x in chunks]
    subprocess.run([sys.executable,str(Path(__file__).parent/"verify_evidence.py"),str(a.staging),str(extracts)],check=True)
    report=reports/"promotion-report.md"
    subprocess.run([sys.executable,str(Path(__file__).parent/"promote.py"),str(a.staging),str(a.target),"--extracts",str(extracts),"--report",str(report)],check=True)
    print(f"Promotion dry-run report: {report}")
    queue=report.parent/"judgment_queue.jsonl"; prompt=Path(__file__).parent/"JUDGMENT_PROMPT.md"
    print(f"Judgment queue: {queue}")
    print(f"Judgment prompt: {prompt}")
    print(f"After judgment: {sys.executable} {Path(__file__).parent/'promote.py'} {a.staging} {a.target} --extracts {extracts} --report {report.parent/'apply-report.md'} --apply --judgments <judgments.jsonl>")
if __name__=="__main__": main()
