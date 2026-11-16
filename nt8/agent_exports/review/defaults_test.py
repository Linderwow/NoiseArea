import os, re, sys, json
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
LIVE = os.path.join(os.path.dirname(os.path.dirname(HERE)), 'bin', 'Custom', 'Strategies', 'NoiseAreaStrategy.cs')
PATH = sys.argv[1] if len(sys.argv) > 1 else LIVE
FAIL = []


def check(name, ok, info=''):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{info}]" if not ok and info else '')); ok or FAIL.append(name)


src = open(PATH, encoding='utf-8').read()
block = re.search(r'State\s*==\s*State\.SetDefaults\)\s*\{(.*?)\n\t\t\t\}', src, re.S)
check('D0 SetDefaults block found', block is not None)
vals = dict(re.findall(r'^\s*(\w+)\s*=\s*([^;]+);', block.group(1), re.M)) if block else {}
reg = next(a for a in json.load(open(os.path.join(HERE, 'accounts.json'), encoding='utf-8'))['accounts'] if a['account'] == 'Sim101')
want = {
    'Contracts': str(reg['contracts']),
    'ChandelierAtr': f"{float(reg.get('chandelier', 0)):.1f}",
    'TrendFilter': 'true' if reg.get('trend_filter', 0) > 0 else 'false',
    'ExitGateBars': str(reg.get('exit_gate', 0)),
    'LunchPause': 'true' if reg.get('lunch_pause', 0) > 0 else 'false',
    'Ema20Confirm': 'true' if reg.get('ema20_confirm', 0) > 0 else 'false',
    'ActivityAfterDays': str(reg.get('activity_after', 0)),
    'TrendFast': '50', 'TrendSlow': '200', 'ChandelierPeriod': '14', 'LookbackDays': '14', 'VolMultiplier': '1.0',
    'StartBehavior': 'StartBehavior.WaitUntilFlatSynchronizeAccount', 'IsExitOnSessionCloseStrategy': 'true',
    'Calculate': 'Calculate.OnBarClose', 'MaxDailyLoss': '0',
}
for i, (k, v) in enumerate(want.items(), 1):
    check(f"D{i} default {k} = {v} (Sim101 registration / live setup)", vals.get(k, '').strip() == v, f"found {vals.get(k)!r}")
guard = re.search(r'BarsPeriod\.BarsPeriodType\s*!=\s*BarsPeriodType\.Minute\s*\|\|\s*BarsPeriod\.Value\s*!=\s*5\)\s*\n\s*blockNote', src)
check(f"D{len(want) + 1} a data series other than 5 Minute refuses to start", guard is not None)
first = src.find('BarsPeriod.BarsPeriodType != BarsPeriodType.Minute'); contracts = src.find('reg > 0 && reg != Contracts')
check(f"D{len(want) + 2} the bar-size check runs before the registration checks", 0 < first < contracts, f"{first} vs {contracts}")
print(f"{PATH}\n{'ALL PASS' if not FAIL else 'FAILED: ' + ', '.join(FAIL)}")
sys.exit(1 if FAIL else 0)
