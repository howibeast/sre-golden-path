// Load profile for the balance read.
//
// Production shape, from the ticket:
//   steady ~150 req/s, spike ~1500 req/s when the Sunday 16:30 event starts.
//
// Local default is 30 req/s so a laptop can show the path. Set SCENARIO and RATE
// when you want the real numbers. The SLO and the capacity note in DECISIONS.md
// are about the production rates, not the laptop default.
//
//   docker compose --profile load run --rm k6
//   docker compose --profile load run --rm -e SCENARIO=spike -e RATE=150 k6

import http from "k6/http";
import { check } from "k6";

const base = __ENV.BASE_URL || "http://account-balance:8080";
const scenario = __ENV.SCENARIO || "steady";
const rate = Number(__ENV.RATE || (scenario === "spike" ? 150 : 30));
const duration = __ENV.DURATION || (scenario === "spike" ? "1m" : "2m");

export const options = {
  scenarios: {
    balance: {
      executor: "constant-arrival-rate",
      rate: rate,
      timeUnit: "1s",
      duration: duration,
      preAllocatedVUs: 20,
      maxVUs: 400,
    },
  },
  thresholds: {
    // Local runs are noisy. The production SLO is 99.9% under 200ms, evaluated
    // by the burn-rate rules, not by this threshold. This line only fails a
    // local run that is clearly broken.
    http_req_failed: ["rate<0.05"],
  },
};

const accounts = [
  "acc_1001",
  "acc_1001",
  "acc_1001",
  "acc_1001",
  "acc_1002",
  "acc_1003",
  "acc_missing",
];

export default function () {
  const account = accounts[Math.floor(Math.random() * accounts.length)];
  const balance = http.get(`${base}/accounts/${account}/balance`);
  check(balance, {
    "balance responded": (response) => response.status === 200 || response.status === 404,
  });
  if (Math.random() < 0.2) {
    http.get(`${base}/accounts/${account}/transactions?limit=20`);
  }
}
