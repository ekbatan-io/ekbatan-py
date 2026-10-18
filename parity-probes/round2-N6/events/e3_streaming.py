"""N6-C e3: event-streaming.md's consumer-side snippets and the namespace rule, run as written."""
import dataclasses
from ekbatan.streaming import ActionEvent
from ekbatan.action import ActionExecutor


def p(k, v):
    print(f"PROBE {k}: {v}", flush=True)


p("actionevent.fields", [f.name for f in dataclasses.fields(ActionEvent)])
p("actionevent.field_count", len(dataclasses.fields(ActionEvent)))
row = {"id": "00000000-0000-0000-0000-000000000001", "namespace": "com.example.finance", "action_id": "00000000-0000-0000-0000-000000000002", "action_name": "DepositMoneyAction",
       "action_params": {"amount": 10.5}, "started_date": "2026-01-01T10:00:00Z",
       "completion_date": "2026-01-01T10:00:01Z", "model_id": "w1", "model_type": "Wallet",
       "event_type": "MoneyDepositedEvent", "payload": {"amount": 10.50, "newBalance": 110.5},
       "event_date": "2026-01-01T10:00:00Z", "delivered": False}
ev = ActionEvent.from_row(row)
p("from_row.payload_amount", repr(ev.payload["amount"]))
p("from_row.event_date", repr(ev.event_date))
msg = dict(row, payload='{"amount":10.50}', action_params='{"amount":10.5}',
           started_date=1767261600000000, completion_date=1767261601000000, event_date=1767261600000000)
ev2 = ActionEvent.from_message(msg, json_columns_are_strings=True, timestamps_are_epoch_micros=True)
p("from_message.payload_amount", repr(ev2.payload["amount"]))
p("from_message.event_date", repr(ev2.event_date))
sentinel = dict(row, model_id=None, model_type=None, event_type=None, payload=None)
p("from_row.sentinel", ActionEvent.from_row(sentinel).event_type)
from ekbatan.action.executor import _valid_namespace
for ns in ("my-service", "my_service", "com.example.finance"):
    try:
        _valid_namespace(ns)
        p(f"namespace.{ns}", "accepted")
    except Exception as e:
        p(f"namespace.{ns}", f"refused {type(e).__name__}")
