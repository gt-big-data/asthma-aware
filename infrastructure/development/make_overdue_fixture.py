"""Create a still-valid, deliberately overdue synthetic fixture for an alarm drill."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
from forecast_job.publication import encode, validate
from forecast_job.sample import generate_sample_forecast

path = Path(sys.argv[1])
payload = generate_sample_forecast('overdue-probe-' + str(uuid4()),
                                   datetime.now(timezone.utc) - timedelta(hours=27),
                                   profile='available')
validate(payload)
path.write_bytes(encode(payload))
print(payload['run_id'])
