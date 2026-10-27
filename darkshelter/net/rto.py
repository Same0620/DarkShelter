"""Retransmission timeout estimator: Jacobson/Karels with a capped backoff (SPEC 8.2).

- First sample:  SRTT = R, RTTVAR = R/2
- Later samples: RTTVAR = 3/4 RTTVAR + 1/4 |SRTT - R|, then SRTT = 7/8 SRTT + 1/8 R
- rto_base = clamp(SRTT + max(0.01, 4 RTTVAR), rto_min, rto_max); initially rto_initial
- on_timeout() doubles the current RTO, capped at backoff_cap x rto_base and rto_max
- a valid sample resets RTO to rto_base; reset() returns to the initial state
- Karn's rule (sample_if): sample only datagrams whose units were all on their first try,
  with the echoed boot_id equal to the current boot.
"""


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


class RTO:
    def __init__(self, initial=3.0, rto_min=0.5, rto_max=30.0, backoff_cap=2.0):
        self.initial = float(initial)
        self.rto_min = float(rto_min)
        self.rto_max = float(rto_max)
        self.backoff_cap = float(backoff_cap)
        self.reset()

    @classmethod
    def from_config(cls, net):
        return cls(net.get("rto_initial_s", 3.0), net.get("rto_min_s", 0.5), net.get("rto_max_s", 30.0),
                   net.get("rto_backoff_cap", 2.0))

    def reset(self):
        self.srtt = None
        self.rttvar = None
        self.rto_base = _clamp(self.initial, self.rto_min, self.rto_max)
        self.rto = self.rto_base
        self.samples = 0
        self.timeouts = 0
        self.max_rto_base = self.rto_base

    def sample(self, r):
        """Feed one RTT sample (seconds). Negative or NaN samples are ignored."""
        try:
            r = float(r)
        except (TypeError, ValueError):
            return False
        if not (r >= 0.0):  # also rejects NaN
            return False
        if self.srtt is None:
            self.srtt = r
            self.rttvar = r / 2.0
        else:
            self.rttvar = 0.75 * self.rttvar + 0.25 * abs(self.srtt - r)
            self.srtt = 0.875 * self.srtt + 0.125 * r
        self.rto_base = _clamp(self.srtt + max(0.01, 4.0 * self.rttvar), self.rto_min, self.rto_max)
        self.rto = self.rto_base
        self.samples += 1
        self.max_rto_base = max(self.max_rto_base, self.rto_base)
        return True

    @staticmethod
    def karn_ok(first_try, echoed_boot, current_boot):
        return bool(first_try) and echoed_boot == current_boot

    def sample_if(self, r, first_try, echoed_boot, current_boot):
        """Apply Karn's rule, then sample. Returns True if the sample was used."""
        if not self.karn_ok(first_try, echoed_boot, current_boot):
            return False
        return self.sample(r)

    def on_timeout(self):
        self.timeouts += 1
        self.rto = min(self.rto * 2.0, self.backoff_cap * self.rto_base, self.rto_max)
        return self.rto

    def stats(self):
        return {"srtt": self.srtt, "rttvar": self.rttvar, "rto_base": self.rto_base, "rto": self.rto,
                "samples": self.samples, "timeouts": self.timeouts, "max_rto_base": self.max_rto_base}
