import sys, datetime as dt
sys.path.insert(0,'/root/v9'); sys.path.insert(0,'/root/v9/tools')
import v9_pull_latest_data as V
print("ROLL_DAYS =", V.ROLL_DAYS)
print(f"{'as-of date':<12}{'front':>8}{'prev':>8}{'roll boundary':>16}  straddle?")
cases = [dt.date(2026,6,5), dt.date(2026,6,11), dt.date(2026,6,12), dt.date(2026,6,17),
         dt.date(2026,8,28), dt.date(2026,9,9), dt.date(2026,9,10), dt.date(2026,9,15),
         dt.date(2026,9,20), dt.date(2026,12,12)]
for d in cases:
    f = V._polygon_front_ticker(d); p = V._polygon_prev_ticker(d); r = V._roll_boundary_utc(d)
    win_start = dt.datetime(d.year,d.month,d.day, tzinfo=dt.timezone.utc) - dt.timedelta(days=7)
    straddle = (r is not None and win_start < r)
    print(f"{str(d):<12}{f:>8}{str(p):>8}{str(r.date()) if r else '-':>16}  {'YES -> splice prev' if straddle else 'no  -> unchanged'}")
print()
print("today's live values:")
print("  front =", V._polygon_front_ticker(), " prev =", V._polygon_prev_ticker(),
      " roll =", V._roll_boundary_utc().date())
