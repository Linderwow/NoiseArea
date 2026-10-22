r"""Entry point for the SOURCES2 program: loads every registered strategy batch, then runs a stage of src2_test.
    python src2_run.py smoke [ids...]     3 random traded sessions per strategy (hand check; no totals)
    python src2_run.py stage1             -> src2_stage1.csv, src2_stage1.txt
    python src2_run.py stage2             -> src2_stage2_variants.csv, src2_stage2.txt
"""
import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import src2_test as T
T.load_batches()

if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    if cmd == 'smoke': T.smoke(sys.argv[2:] or None)
    elif cmd == 'stage1': T.stage1(only_new='--new' in sys.argv)
    elif cmd == 'stage2': T.stage2() if len(sys.argv) < 3 else T.stage2(prefix=sys.argv[2], prior=sys.argv[3] if len(sys.argv) > 3 else None)
    else: print(__doc__)
