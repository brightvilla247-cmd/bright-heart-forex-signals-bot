
import os
import json
import requests
import pandas as pd

TWELVE_DATA_KEY = os.getenv("TWELVE_DATA_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

PAIRS = [
    "EUR/USD", "GBP/USD", "USD/JPY",
    "AUD/USD", "USD/CAD", "USD/CHF", "NZD/USD"
]

STATE_FILE = "state.json"


def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def get_data(pair):
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": pair,
        "interval": "1h",
        "outputsize": 120,
        "apikey": TWELVE_DATA_KEY
    }

    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    result = response.json()

    if "values" not in result:
        raise ValueError(result.get("message", "Market data unavailable"))

    df = pd.DataFrame(result["values"])
    df["datetime"] = pd.to_datetime(df["datetime"])
    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col])

    df = df.sort_values("datetime").reset_index(drop=True)

    # Exclude the newest candle because it may still be forming.
    return df.iloc[:-1].copy()


def analyse(pair):
    df = get_data(pair)

    if len(df) < 60:
        return None

    close = df["close"]
    high = df["high"]
    low = df["low"]

    df["ema20"] = close.ewm(span=20, adjust=False).mean()
    df["ema50"] = close.ewm(span=50, adjust=False).mean()

    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    df["rsi"] = 100 - (100 / (1 + rs))

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["signal"] = df["macd"].ewm(span=9, adjust=False).mean()

    previous_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - previous_close).abs(),
        (low - previous_close).abs()
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/14, adjust=False).mean()

    last = df.iloc[-1]
    price = float(last["close"])
    rsi = float(last["rsi"])
    ema20 = float(last["ema20"])
    ema50 = float(last["ema50"])
    macd = float(last["macd"])
    signal = float(last["signal"])
    atr_value = float(atr.iloc[-1])

    if atr_value <= 0:
        return None

    buy_checks = [
        ema20 > ema50,
        price > ema20,
        50 < rsi < 70,
        macd > signal,
        price > ema50
    ]

    sell_checks = [
        ema20 < ema50,
        price < ema20,
        30 < rsi < 50,
        macd < signal,
        price < ema50
    ]

    buy_score = sum(buy_checks)
    sell_score = sum(sell_checks)

    if buy_score >= 4:
        direction = "BUY"
        score = buy_score
        entry = price
        sl = entry - (1.5 * atr_value)
        tp1 = entry + (1.5 * atr_value)
        tp2 = entry + (2.5 * atr_value)

    elif sell_score >= 4:
        direction = "SELL"
        score = sell_score
        entry = price
        sl = entry + (1.5 * atr_value)
        tp1 = entry - (1.5 * atr_value)
        tp2 = entry - (2.5 * atr_value)

    else:
        return None

    return {
        "pair": pair,
        "direction": direction,
        "score": score,
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "rsi": rsi,
        "candle": str(last["datetime"])
    }


def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    response = requests.post(
        url,
        data={"chat_id": TELEGRAM_CHAT_ID, "text": message},
        timeout=30
    )
    response.raise_for_status()


def main():
    if not all([TWELVE_DATA_KEY, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID]):
        raise RuntimeError("Missing required environment variables.")

    state = load_state()

    for pair in PAIRS:
        try:
            signal = analyse(pair)

            if not signal:
                print(f"{pair}: WAIT - no confirmed setup")
                continue

            key = f"{pair}_{signal['direction']}"
            if state.get(key) == signal["candle"]:
                print(f"{pair}: duplicate signal skipped")
                continue

            message = (
                "💠 BRIGHT HEART AI FOREX SIGNALS\n\n"
                f"Pair: {signal['pair']}\n"
                f"Signal: {signal['direction']}\n"
                f"Timeframe: 1 Hour\n"
                f"Entry: {signal['entry']:.5f}\n"
                f"Stop Loss: {signal['sl']:.5f}\n"
                f"Take Profit 1: {signal['tp1']:.5f}\n"
                f"Take Profit 2: {signal['tp2']:.5f}\n"
                f"Rule score: {signal['score']}/5\n"
                f"RSI: {signal['rsi']:.1f}\n"
                f"Candle: {signal['candle']}\n\n"
                "Analysis-based alert, not a guaranteed result. "
                "Check spreads and risk before trading."
            )

            send_telegram(message)
            state[key] = signal["candle"]
            save_state(state)
            print(f"{pair}: {signal['direction']} alert sent")

        except Exception as error:
            print(f"{pair}: ERROR - {error}")


if __name__ == "__main__":
    main()
  
