# Hermes Protocol Packets

One packet per line, newline terminated.

SENS,up=<seconds>,rssi=<rssi_or_999>,heap=<bytes>,psram=<bytes>,ct=<temp_c_or_nan>,n=<frame>,light=<0-1>,scene=<0-1>,mic=<0-1>,micpk=<0-1>,micnf=<0-1>

Example:
SENS,up=27,rssi=999,heap=249304,psram=8373944,ct=35.4,n=42,light=0.31,scene=0.07,mic=0.04,micpk=0.21,micnf=0.02

## Logging JSONL (Odroid)

One JSON object per second.

Example schema:
{"ts":1700000000,"t":23.4,"rh":45.2,"eco2":550,"tvoc":12,"esp":{"n":42,"rssi":999,"heap":369960,"psram":8386035,"ct":37.4,"light":0.31,"scene":0.07},"link":{"bps":55,"age":120,"parseFail":3}}
