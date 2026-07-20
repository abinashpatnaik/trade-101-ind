import pytz
from datetime import datetime, time

tz = pytz.timezone("America/New_York")
now_ny = datetime.now(tz=tz)
print("NY time:", now_ny, "time:", now_ny.time())

# Simulate a specific UTC time (8 AM UK time)
# 8 AM UK time in June (BST) is 7 AM UTC
utc_now = datetime.utcnow().replace(hour=7, minute=0, second=0, tzinfo=pytz.UTC)
ny_time_at_8am_uk = utc_now.astimezone(tz)
print("At 8 AM UK time (BST), NY time is:", ny_time_at_8am_uk)
print("Is pre-market?", time(8,0) <= ny_time_at_8am_uk.time() < time(9,30))

# Simulate 1 PM UK time (BST) -> 12 PM UTC
utc_now2 = datetime.utcnow().replace(hour=12, minute=0, second=0, tzinfo=pytz.UTC)
ny_time_at_1pm_uk = utc_now2.astimezone(tz)
print("At 1 PM UK time (BST), NY time is:", ny_time_at_1pm_uk)
print("Is pre-market?", time(8,0) <= ny_time_at_1pm_uk.time() < time(9,30))

