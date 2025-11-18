"""
=============================================================================
FILE: main.py - PHIÊN BẢN CẢI TIẾN
=============================================================================
Professional Trading Bot with 16 Smart Money Concepts Combos
- ĐÃ SỬA: Thread-safety với locks
- ĐÃ SỬA: Series comparison errors
- ĐÃ SỬA: UTC timezone handling
- ĐÃ SỬA: iloc slicing
- ĐÃ SỬA: Combo 13, 14 hoàn chỉnh
- ĐÃ THÊM: Combo 15, 16 mới
=============================================================================
"""

from dotenv import load_dotenv
load_dotenv()

import telebot
from telebot import types
import requests
import pandas as pd
import json
import os
from datetime import datetime, timedelta, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from ta.trend import MACD, EMAIndicator
from ta.momentum import RSIIndicator
from ta.volatility import BollingerBands, AverageTrueRange
import threading
from flask import Flask, jsonify
import logging
import time
import numpy as np

# =============================================================================
# CONFIGURATION & LOGGING
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Environment variables with validation
TOKEN = os.getenv("TOKEN", "8505137563:AAGuvTtObBIILgjsUnP8v-8EsBRKn2T3h4E")

ADMIN_ID_ENV = os.getenv("ADMIN_ID", "7760459637")
if ADMIN_ID_ENV is None or ADMIN_ID_ENV == "":
    logger.warning("⚠️ ADMIN_ID not set; admin features disabled.")
    ADMIN_ID = None
else:
    ADMIN_ID = int(ADMIN_ID_ENV)
    logger.info(f"✅ Admin ID: {ADMIN_ID}")

# Trading configuration
COINS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "DOGEUSDT", "ADAUSDT", "TRXUSDT", "AVAXUSDT", "SHIBUSDT",
    "LINKUSDT", "DOTUSDT", "NEARUSDT", "LTCUSDT", "UNIUSDT",
    "PEPEUSDT", "ICPUSDT", "APTUSDT", "HBARUSDT", "CROUSDT",
    "VETUSDT", "MKRUSDT", "FILUSDT", "ATOMUSDT", "IMXUSDT",
    "OPUSDT", "ARBUSDT", "INJUSDT", "RUNEUSDT", "GRTUSDT"
]

INTERVAL = os.getenv("INTERVAL", "15m")
LIMIT = int(os.getenv("LIMIT", "300"))
SQUEEZE_THRESHOLD = float(os.getenv("SQUEEZE_THRESHOLD", "0.018"))
COOLDOWN_MINUTES = int(os.getenv("COOLDOWN_MINUTES", "60"))
SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", "6"))
RISK_PER_TRADE = float(os.getenv("RISK_PER_TRADE", "0.01"))

# Flask app for health check
app = Flask(__name__)
bot = telebot.TeleBot(TOKEN)

# Thread-safety lock
data_lock = threading.Lock()

# Global stats
stats = {
    "last_scan_time": None,
    "last_signal_count": 0,
    "total_signals_sent": 0,
    "uptime_start": datetime.now(timezone.utc)
}

# THÊM SAU PHẦN CONFIGURATION
def validate_environment():
    """Validate critical environment variables"""
    if not TOKEN or TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.error("❌ BOT TOKEN chưa được config! Vui lòng set environment variable TOKEN")
        return False
    
    if ADMIN_ID == 0:
        logger.warning("⚠️ ADMIN_ID chưa được config. Admin features sẽ bị disabled")
    
    required_vars = {
        "TOKEN": TOKEN,
        "INTERVAL": INTERVAL,
        "LIMIT": LIMIT,
        "COINS_COUNT": len(COINS)
    }
    
    logger.info("✅ Environment validation passed:")
    for key, value in required_vars.items():
        logger.info(f"   {key}: {value}")
    
    return True
  
# =============================================================================
# FILE STORAGE FUNCTIONS (Thread-safe)
# =============================================================================

def load_json(file):
    """Load JSON file with error recovery"""
    if not os.path.exists(file):
        if file == "users.json":
            data = {"users": {}}
        elif file == "recent_signals.json":
            data = {"signals": []}
        elif file == "results.json":
            data = {"results": []}
        else:
            data = {}
        save_json(file, data)
        return data
    
    try:
        with open(file, "r", encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Error reading {file}: {e}. Creating new file.")
        if file == "users.json":
            data = {"users": {}}
        elif file == "recent_signals.json":
            data = {"signals": []}
        elif file == "results.json":
            data = {"results": []}
        else:
            data = {}
        save_json(file, data)
        return data

def save_json(file, data):
    """Save JSON file with atomic write (call within lock)"""
    temp_file = f"{file}.tmp"
    try:
        with open(temp_file, "w", encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        os.replace(temp_file, file)
    except Exception as e:
        logger.error(f"Error saving {file}: {e}")
        if os.path.exists(temp_file):
            os.remove(temp_file)

# Load data
users_data = load_json("users.json")
recent_signals = load_json("recent_signals.json")
results_data = load_json("results.json")

# =============================================================================
# BINANCE API FUNCTIONS
# =============================================================================

def get_klines(symbol, max_retries=3):
    """Fetch klines from Binance Futures API with retry mechanism"""
    url = f"https://fapi.binance.com/fapi/v1/klines"
    params = {
        "symbol": symbol,
        "interval": INTERVAL,
        "limit": LIMIT
    }
    
    for attempt in range(max_retries):
        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            df = pd.DataFrame(data, columns=[
                "open_time", "open", "high", "low", "close", "volume",
                "close_time", "quote_volume", "trades", "taker_buy_base",
                "taker_buy_quote", "ignore"
            ])
            
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = df[col].astype(float)
            
            df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
            
            logger.info(f"✅ Fetched {len(df)} candles for {symbol}")
            return df
            
        except Exception as e:
            logger.error(f"Attempt {attempt + 1}/{max_retries} failed for {symbol}: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            else:
                return None
    
    return None

# =============================================================================
# INDICATOR CALCULATIONS
# =============================================================================

def add_indicators(df):
    """Add all technical indicators to dataframe"""
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]
    
    # EMAs
    df["ema8"] = EMAIndicator(close, window=8).ema_indicator()
    df["ema21"] = EMAIndicator(close, window=21).ema_indicator()
    df["ema50"] = EMAIndicator(close, window=50).ema_indicator()
    df["ema200"] = EMAIndicator(close, window=200).ema_indicator()
    
    # MACD
    macd = MACD(close)
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()
    df["macd_hist"] = macd.macd_diff()
    
    # RSI
    df["rsi14"] = RSIIndicator(close, window=14).rsi()
    
    # Bollinger Bands
    bb = BollingerBands(close, window=20, window_dev=2)
    df["bb_upper"] = bb.bollinger_hband()
    df["bb_lower"] = bb.bollinger_lband()
    df["bb_mid"] = bb.bollinger_mavg()
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]
    
    # ATR
    atr = AverageTrueRange(high, low, close, window=14)
    df["atr"] = atr.average_true_range()
    
    # Keltner Channel
    typical_price = (high + low + close) / 3
    df["kc_mid"] = typical_price.rolling(20).mean()
    df["kc_range"] = df["atr"] * 1.5
    df["kc_upper"] = df["kc_mid"] + df["kc_range"]
    df["kc_lower"] = df["kc_mid"] - df["kc_range"]
    
    # VWAP (session approximation)
    df["vwap"] = (typical_price * volume).cumsum() / volume.cumsum()
    
    # Volume MA
    df["volume_ma20"] = volume.rolling(20).mean()
    
    # FVG Detection
    df["fvg_bull"] = (df["low"].shift(2) > df["high"].shift(1))
    df["fvg_bear"] = (df["high"].shift(2) < df["low"].shift(1))
    
    # Wick and Body
    df["body"] = abs(df["open"] - df["close"])
    df["upper_wick"] = df["high"] - df[["open", "close"]].max(axis=1)
    df["lower_wick"] = df[["open", "close"]].min(axis=1) - df["low"]
    
    return df

# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def calculate_position_size(equity, entry, sl, risk_per_trade=RISK_PER_TRADE):
    """Calculate position size based on risk management"""
    if equity <= 0 or entry <= 0 or sl <= 0:
        return 0, 0
    
    risk_amount = equity * risk_per_trade
    price_diff = abs(entry - sl)
    
    if price_diff == 0:
        return 0, 0
    
    position_notional = risk_amount / (price_diff / entry)
    qty = position_notional / entry
    
    return round(position_notional, 2), round(qty, 6)

def check_cooldown(symbol, combo_name):
    """Check if signal is in cooldown period (UTC-aware)"""
    now = datetime.now(timezone.utc)
    
    with data_lock:
        for sig in recent_signals.get("signals", []):
            if sig["coin"] == symbol and sig.get("combo") == combo_name:
                # Parse UTC time
                sig_time = datetime.strptime(sig["time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                elapsed_minutes = (now - sig_time).total_seconds() / 60
                
                if elapsed_minutes < COOLDOWN_MINUTES:
                    logger.info(f"⏳ Cooldown: {symbol} - {combo_name}: {elapsed_minutes:.1f}/{COOLDOWN_MINUTES} min")
                    return False
    
    return True

# =============================================================================
# 16 TRADING COMBOS (ĐÃ SỬA LỖI)
# =============================================================================

def combo1_fvg_squeeze_pro(df):
    """FVG Squeeze Pro"""
    try:
        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        squeeze = (last.bb_width < SQUEEZE_THRESHOLD and 
                  last.bb_upper < last.kc_upper and 
                  last.bb_lower > last.kc_lower)
        breakout_up = last.close > last.bb_upper and prev.close <= prev.bb_upper
        vol_spike = last.volume > last.volume_ma20 * 1.3  # ✅ SỬA: dùng last.volume_ma20
        trend_up = last.close > last.ema200
        rsi_ok = last.rsi14 < 68
        
        if squeeze and breakout_up and vol_spike and trend_up and rsi_ok:
            entry = last.close
            sl = entry - 1.5 * last.atr
            tp = entry + 3.0 * last.atr
            return "LONG", entry, sl, tp, "FVG Squeeze Pro"
        
        breakout_down = last.close < last.bb_lower and prev.close >= prev.bb_lower
        if squeeze and breakout_down and vol_spike and last.close < last.ema200:
            entry = last.close
            sl = entry + 1.5 * last.atr
            tp = entry - 3.0 * last.atr
            return "SHORT", entry, sl, tp, "FVG Squeeze Pro"
            
    except Exception as e:
        logger.error(f"Combo1 error: {e}")
    
    return None

def combo2_macd_ob_retest(df):
    """MACD Order Block Retest"""
    try:
        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        macd_cross_up = last.macd > last.macd_signal and prev.macd <= prev.macd_signal
        price_above_ema200 = last.close > last.ema200
        
        ob_zone = None
        if all(df["close"].iloc[-3:] > df["open"].iloc[-3:]):  # ✅ SỬA: dùng .iloc
            ob_zone = df["low"].iloc[-5:-2].min()  # ✅ SỬA: dùng .iloc
        
        retest = ob_zone is not None and last.low <= ob_zone + last.atr * 0.5
        vol_confirm = last.volume > df["volume"].mean() * 1.1
        
        if macd_cross_up and price_above_ema200 and retest and vol_confirm:
            entry = last.close
            sl = ob_zone - last.atr
            tp = entry + 2.5 * last.atr
            return "LONG", entry, sl, tp, "MACD Order Block Retest"
            
    except Exception as e:
        logger.error(f"Combo2 error: {e}")
    
    return None

def combo3_stop_hunt_squeeze(df):
    """Stop Hunt Squeeze"""
    try:
        last = df.iloc[-1]
        
        squeeze = last.bb_width < SQUEEZE_THRESHOLD
        stop_hunt = False
        
        if last.body > 0:
            if last.close > last.open:
                stop_hunt = (last.lower_wick / last.body > 2)
            else:
                stop_hunt = (last.upper_wick / last.body > 2)
        
        breakout_up = last.close > last.bb_upper
        
        if squeeze and stop_hunt and breakout_up:
            entry = last.close
            sl = last.low - last.atr
            tp = entry + 2.8 * last.atr
            return "LONG", entry, sl, tp, "Stop Hunt Squeeze"
            
    except Exception as e:
        logger.error(f"Combo3 error: {e}")
    
    return None

def combo4_fvg_ema_pullback(df):
    """FVG EMA Pullback"""
    try:
        last = df.iloc[-1]
        
        fvg_bull_zones = df[df["fvg_bull"]]
        fvg_pullback = False
        
        if not fvg_bull_zones.empty and df["fvg_bull"].iloc[-5:].any():  # ✅ SỬA: .iloc + .any()
            fvg_pullback = last.low <= fvg_bull_zones["high"].max()
        
        cross_up = last.ema8 > last.ema21 and df["ema8"].iloc[-2] <= df["ema21"].iloc[-2]
        
        if fvg_pullback and cross_up:
            entry = last.close
            sl = last.low - last.atr * 0.8
            tp = entry + 2.0 * last.atr
            return "LONG", entry, sl, tp, "FVG EMA Pullback"
            
    except Exception as e:
        logger.error(f"Combo4 error: {e}")
    
    return None

def combo5_fvg_macd_divergence(df):
    """FVG + MACD Divergence"""
    try:
        last = df.iloc[-1]
        
        hist = df["macd_hist"]
        low = df["low"]
        
        divergence = hist.iloc[-1] > hist.iloc[-3] and low.iloc[-1] < low.iloc[-3]
        fvg = df["fvg_bull"].iloc[-8:].any()  # ✅ SỬA: .iloc + .any()
        rsi_ok = last.rsi14 < 30
        
        if divergence and fvg and rsi_ok:
            entry = last.close
            sl = low.iloc[-5:].min() - last.atr  # ✅ SỬA: .iloc
            tp = entry + 2.5 * last.atr
            return "LONG", entry, sl, tp, "FVG + MACD Divergence"
            
    except Exception as e:
        logger.error(f"Combo5 error: {e}")
    
    return None

def combo6_ob_liquidity_grab(df):
    """Order Block + Liquidity Grab"""
    try:
        last = df.iloc[-1]
        
        ob = df["low"].iloc[-6:-3].min()  # ✅ SỬA: .iloc
        liquidity_grab = (last.lower_wick / last.body > 2.5) if last.body > 0 else False
        retest_ob = last.close > ob
        macd_pos = last.macd_hist > 0
        
        if liquidity_grab and retest_ob and macd_pos:
            entry = last.close
            sl = last.low - last.atr
            tp = entry + 1.8 * last.atr
            return "LONG", entry, sl, tp, "Order Block + Liquidity Grab"
            
    except Exception as e:
        logger.error(f"Combo6 error: {e}")
    
    return None

def combo7_stop_hunt_fvg_retest(df):
    """Stop Hunt + FVG Retest"""
    try:
        last = df.iloc[-1]
        
        stop_hunt = (last.lower_wick / last.body > 2) if last.body > 0 else False
        fvg_after = df["fvg_bull"].iloc[-3:]  # ✅ SỬA: .iloc
        retest = (last.low <= df["high"].shift(1).max()) if fvg_after.any() else False  # ✅ SỬA: .any()
        
        if stop_hunt and fvg_after.any() and retest:
            entry = last.close
            sl = last.low - 0.5 * last.atr
            tp = entry + 1.5 * last.atr
            return "LONG", entry, sl, tp, "Stop Hunt + FVG Retest"
            
    except Exception as e:
        logger.error(f"Combo7 error: {e}")
    
    return None

def combo8_fvg_macd_hist_spike(df):
    """FVG + MACD Hist Spike"""
    try:
        last = df.iloc[-1]
        
        # ❌ HIỆN TẠI: Có thể gặp lỗi shape không khớp
        # hist_spike = (df["macd_hist"].iloc[-3:].values > df["macd_hist"].iloc[-4:-1].values).all()
        
        # ✅ SỬA THÀNH:
        if len(df) >= 5:
            current_hist = df["macd_hist"].iloc[-3:].values
            prev_hist = df["macd_hist"].iloc[-4:-1].values
            if len(current_hist) == 3 and len(prev_hist) == 3:
                hist_spike = (current_hist > prev_hist).all()
            else:
                hist_spike = False
        else:
            hist_spike = False
            
        fvg = df["fvg_bull"].iloc[-5:].any()
        price_above_vwap = last.close > last.vwap
        
        if hist_spike and fvg and price_above_vwap:
            entry = last.close
            sl = last.low - last.atr
            tp = entry + 2.5 * last.atr
            return "LONG", entry, sl, tp, "FVG + MACD Hist Spike"
            
    except Exception as e:
        logger.error(f"Combo8 error: {e}")
    
    return None

def combo9_ob_fvg_confluence(df):
    """OB + FVG Confluence"""
    try:
        last = df.iloc[-1]
        
        ob = df["low"].iloc[-10:-5].min()  # ✅ SỬA: .iloc
        fvg_bull_zones = df[df["fvg_bull"]]
        fvg_zone = 0
        
        if not fvg_bull_zones.empty and df["fvg_bull"].iloc[-10:].any():  # ✅ SỬA: .iloc + .any()
            fvg_zone = fvg_bull_zones["high"].max()
        
        confluence = (abs(ob - fvg_zone) < last.atr * 0.5) if fvg_zone > 0 else False
        engulfing = last.close > last.open and last.open < df["close"].iloc[-2]
        volume_delta = last.volume > df["volume"].mean() * 1.5
        
        if confluence and engulfing and volume_delta:
            entry = last.close
            sl = min(ob, fvg_zone) - last.atr if fvg_zone > 0 else ob - last.atr
            tp = entry + 2.0 * last.atr
            return "LONG", entry, sl, tp, "OB + FVG Confluence"
            
    except Exception as e:
        logger.error(f"Combo9 error: {e}")
    
    return None

def combo10_smc_ultimate(df):
    """SMC Ultimate"""
    try:
        last = df.iloc[-1]
        
        squeeze = last.bb_width < SQUEEZE_THRESHOLD
        fvg = df["fvg_bull"].iloc[-5:].any()  # ✅ SỬA: .iloc + .any()
        macd_up = last.macd_hist > 0 and last.macd_hist > df["macd_hist"].iloc[-2]
        liquidity = (last.lower_wick / last.body > 2) if last.body > 0 else False
        ob_retest = last.low <= df["low"].iloc[-5:-2].min()  # ✅ SỬA: .iloc
        
        if squeeze and fvg and macd_up and liquidity and ob_retest:
            entry = last.close
            sl = last.low - last.atr
            tp = entry + 3.5 * last.atr
            return "LONG", entry, sl, tp, "SMC Ultimate"
            
    except Exception as e:
        logger.error(f"Combo10 error: {e}")
    
    return None

def combo11_fvg_ob_liquidity_break(df):
    """FVG + Order Block + Liquidity Break"""
    try:
        last = df.iloc[-1]
        
        # FVG bullish
        fvg = last.fvg_bull or df["fvg_bull"].iloc[-3:].any()  # ✅ SỬA: .iloc + .any()
        
        # Order Block
        ob = df["low"].iloc[-5:].min()  # ✅ SỬA: .iloc
        
        # Liquidity Break
        liquidity_break = last.close > df["high"].iloc[-5:].max()  # ✅ SỬA: .iloc
        
        # Volume
        vol_spike = last.volume > last.volume_ma20 * 1.5  # ✅ SỬA: last.volume_ma20
        
        if fvg and liquidity_break and vol_spike:
            entry = last.close
            sl = ob - 0.5 * last.atr
            tp = entry + 2.0 * last.atr
            return "LONG", entry, sl, tp, "FVG OB Liquidity Break"
            
    except Exception as e:
        logger.error(f"Combo11 error: {e}")
    
    return None

def combo12_liquidity_grab_fvg_retest(df):
    """Liquidity Grab + FVG Retest"""
    try:
        last = df.iloc[-1]
        
        # Liquidity Grab
        liquidity_grab = (last.lower_wick / last.body > 2.5) if last.body > 0 else False
        
        # FVG Retest
        fvg_zones = df[df["fvg_bull"]]
        fvg_retest = False
        if not fvg_zones.empty and df["fvg_bull"].iloc[-5:].any():  # ✅ SỬA: .iloc + .any()
            fvg_retest = last.low <= fvg_zones["high"].max()
        
        # MACD
        macd_ok = last.macd_hist > 0 and last.macd_hist > df["macd_hist"].iloc[-2]
        
        if liquidity_grab and fvg_retest and macd_ok:
            entry = last.close
            sl = last.low - 0.8 * last.atr
            tp = entry + 1.8 * last.atr
            return "LONG", entry, sl, tp, "Liquidity Grab FVG Retest"
            
    except Exception as e:
        logger.error(f"Combo12 error: {e}")
    
    return None

def combo13_fvg_macd_momentum_scalp(df):
    """COMBO 13: FVG + MACD Momentum Scalp (✅ ĐÃ SỬA HOÀN CHỈNH)"""
    try:
        last = df.iloc[-1]
        
        # FVG recent
        fvg = df["fvg_bull"].iloc[-2:].any() and last.close > last.open  # ✅ SỬA: .iloc + .any()
        
        # MACD momentum
        macd_mom = last.macd > last.macd_signal and abs(last.macd_hist) > abs(df["macd_hist"].iloc[-2])
        
        # VWAP
        above_vwap = last.close > last.vwap
        
        # Low volatility
        low_vol = (last.atr / last.close) < 0.02
        
        if fvg and macd_mom and above_vwap and low_vol:  # ✅ SỬA: lỗi chính tả "and" thay vì "andkhông"
            entry = last.close
            sl = last.low - 0.5 * last.atr
            tp = entry + 1.2 * last.atr
            return "LONG", entry, sl, tp, "FVG MACD Momentum Scalp"
            
    except Exception as e:
        logger.error(f"Combo13 error: {e}")
    
    return None

def combo14_ob_liquidity_macd_div(df):
    """COMBO 14: Order Block + Liquidity + MACD Divergence (✅ ĐÃ SỬA HOÀN CHỈNH)"""
    try:
        last = df.iloc[-1]
        
        # Order Block
        ob = df["low"].iloc[-7:-2].min()  # ✅ SỬA: .iloc
        
        # Liquidity sweep
        liquidity = (last.lower_wick / last.body > 2.0) if last.body > 0 else False
        
        # MACD Divergence
        divergence = (df["macd_hist"].iloc[-1] > df["macd_hist"].iloc[-3] and 
                     df["low"].iloc[-1] < df["low"].iloc[-3])
        
        # Entry confirmation
        entry_ok = last.close > ob
        
        if liquidity and divergence and entry_ok:
            entry = last.close
            sl = ob - 0.3 * last.atr
            tp = entry + 2.5 * last.atr
            return "LONG", entry, sl, tp, "OB Liquidity MACD Div"  # ✅ SỬA: Thêm return
            
    except Exception as e:
        logger.error(f"Combo14 error: {e}")
    
    return None

def combo15_vwap_ema_volume_scalp(df):
    """COMBO 15: VWAP + EMA Cross + Volume Spike Scalp (✅ MỚI)"""
    try:
        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        # EMA Cross (8 & 21)
        ema_cross = last.ema8 > last.ema21 and prev.ema8 <= prev.ema21
        
        # Price above VWAP
        above_vwap = last.close > last.vwap
        
        # Volume spike (180% of 20-period average)
        vol_spike = last.volume > last.volume_ma20 * 1.8  # ✅ SỬA: last.volume_ma20
        
        # RSI not overbought (below 60)
        rsi_ok = last.rsi14 < 60
        
        if ema_cross and above_vwap and vol_spike and rsi_ok:
            entry = last.close
            sl = last.low - 0.5 * last.atr
            tp = entry + 1.0 * last.atr
            return "LONG", entry, sl, tp, "VWAP EMA Volume Scalp"
            
    except Exception as e:
        logger.error(f"Combo15 error: {e}")
    
    return None

def combo16_rsi_extreme_bounce(df):
    """COMBO 16: RSI Extreme + Price Action Bounce (✅ MỚI)"""
    try:
        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        # RSI Extreme (oversold for long, overbought for short)
        rsi_oversold = last.rsi14 < 25
        rsi_overbought = last.rsi14 > 75
        
        # Price Action Bounce patterns
        bullish_engulfing = (last.close > last.open and 
                           prev.close < prev.open and 
                           last.close > prev.open and 
                           last.open < prev.close)
        
        bearish_engulfing = (last.close < last.open and 
                           prev.close > prev.open and 
                           last.close < prev.open and 
                           last.open > prev.close)
        
        hammer = (last.lower_wick > 2 * last.body and 
                last.upper_wick < 0.2 * last.body and 
                last.close > last.open) if last.body > 0 else False
                
        shooting_star = (last.upper_wick > 2 * last.body and 
                       last.lower_wick < 0.2 * last.body and 
                       last.close < last.open) if last.body > 0 else False
        
        # Volume confirmation
        vol_ok = last.volume > last.volume_ma20 * 1.2  # ✅ SỬA: last.volume_ma20
        
        # LONG: RSI oversold + bullish pattern
        if rsi_oversold and (bullish_engulfing or hammer) and vol_ok:
            entry = last.close
            sl = last.low - 0.8 * last.atr
            tp = entry + 1.5 * last.atr
            return "LONG", entry, sl, tp, "RSI Extreme Bounce LONG"
            
        # SHORT: RSI overbought + bearish pattern  
        if rsi_overbought and (bearish_engulfing or shooting_star) and vol_ok:
            entry = last.close
            sl = last.high + 0.8 * last.atr
            tp = entry - 1.5 * last.atr
            return "SHORT", entry, sl, tp, "RSI Extreme Bounce SHORT"
            
    except Exception as e:
        logger.error(f"Combo16 error: {e}")
    
    return None

# =============================================================================
# MAIN SCANNING FUNCTION
# =============================================================================

def scan():
    """Main scanning function - checks all combos for all coins"""
    logger.info(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] 🔍 Starting scan cycle...")
    stats["last_scan_time"] = datetime.now(timezone.utc)
    signals_found = 0
    
    # All 16 combos
    combos = [
        combo1_fvg_squeeze_pro,
        combo2_macd_ob_retest,
        combo3_stop_hunt_squeeze,
        combo4_fvg_ema_pullback,
        combo5_fvg_macd_divergence,
        combo6_ob_liquidity_grab,
        combo7_stop_hunt_fvg_retest,
        combo8_fvg_macd_hist_spike,
        combo9_ob_fvg_confluence,
        combo10_smc_ultimate,
        combo11_fvg_ob_liquidity_break,
        combo12_liquidity_grab_fvg_retest,
        combo13_fvg_macd_momentum_scalp,
        combo14_ob_liquidity_macd_div,
        combo15_vwap_ema_volume_scalp,
        combo16_rsi_extreme_bounce
    ]
    
    for coin in COINS:
        try:
            # Fetch data
            df = get_klines(coin)
            if df is None or len(df) < 200:
                logger.warning(f"⚠️ Insufficient data for {coin}")
                continue
            
            # Add indicators
            df = add_indicators(df)
            
            # Check each combo
            for combo_func in combos:
                try:
                    result = combo_func(df)
                    
                    if result:
                        direction, entry, sl, tp, combo_name = result
                        
                        # Check cooldown
                        if not check_cooldown(coin, combo_name):
                            continue
                        
                        # Create signal
                        signal_id = f"{coin}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}_{combo_name.replace(' ', '')[:15]}"
                        
                        # Calculate position size suggestion
                        equity_example = 1000  # Example equity
                        notional, qty = calculate_position_size(equity_example, entry, sl)
                        
                        # Format message
                        signal_text = format_signal_message(
                            coin, direction, entry, sl, tp, 
                            combo_name, signal_id, notional, qty
                        )
                        
                        # Save signal (thread-safe)
                        with data_lock:
                            recent_signals["signals"].append({
                                "id": signal_id,
                                "coin": coin,
                                "combo": combo_name,
                                "time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                                "entry": float(entry),
                                "sl": float(sl),
                                "tp": float(tp),
                                "direction": direction,
                                "sent_to": []
                            })
                            save_json("recent_signals.json", recent_signals)
                        
                        # Send to subscribers
                        send_signal_to_subscribers(signal_text, signal_id)
                        
                        signals_found += 1
                        logger.info(f"✅ Signal found: {coin} - {combo_name}")
                        
                        # Break after first signal for this coin
                        break
                        
                except Exception as e:
                    logger.error(f"❌ Error in {combo_func.__name__} for {coin}: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"❌ Error scanning {coin}: {e}")
            continue
    
    stats["last_signal_count"] = signals_found
    stats["total_signals_sent"] += signals_found
    logger.info(f"✅ Scan complete. Found {signals_found} signals.")

def format_signal_message(coin, direction, entry, sl, tp, combo_name, signal_id, notional, qty):
    """Format signal message for Telegram"""
    symbol_clean = coin.replace("USDT", "")
    
    # Risk/Reward calculation
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    rr_ratio = reward / risk if risk > 0 else 0
    
    message = f"""
🔔 <b>#{symbol_clean} — {direction}</b> 📊

<b>📌 Điểm vào lệnh:</b> {entry:.4f}
<b>🛑 Dừng lỗ (SL):</b> {sl:.4f}
<b>🎯 Chốt lời (TP):</b> {tp:.4f}

<b>📈 Tín hiệu:</b> {combo_name}
<b>⏱ Khung thời gian:</b> {INTERVAL}
<b>💹 Risk/Reward:</b> 1:{rr_ratio:.2f}

<b>💰 Gợi ý Position Size (Equity $1000, Risk 1%):</b>
  • Notional: ${notional:.2f}
  • Quantity: {qty:.6f} {symbol_clean}

<b>⚠️ Quy tắc vàng:</b>
• Tuân thủ quản lý vốn nghiêm ngặt
• Không FOMO, không revenge trade
• Chỉ risk 0.5-1% mỗi lệnh
• Luôn đặt SL ngay khi vào lệnh

<b>🆔 Signal ID:</b> <code>{signal_id}</code>
<b>🕐 Thời gian:</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC

<i>⚡ Đây là tín hiệu tham khảo, không phải lời khuyên đầu tư.</i>
"""
    return message

def send_signal_to_subscribers(signal_text, signal_id):
    """Send signal to all subscribed users (thread-safe)"""
    sent_count = 0
    
    with data_lock:
        users = users_data.get("users", {}).copy()  # Copy to avoid lock issues
    
    for uid, user_data in users.items():
        try:
            # Check if subscribed
            if not user_data.get("subscribed", True):
                continue
            
            # Check watchlist
            watchlist = user_data.get("watchlist", [])
            if watchlist:
                # Extract coin from signal
                coin = signal_id.split("_")[0]
                if coin not in watchlist:
                    continue
            
            # Prepare markup (admin only)
            markup = None
            if ADMIN_ID is not None and int(uid) == ADMIN_ID:
                markup = types.InlineKeyboardMarkup(row_width=2)
                markup.add(
                    types.InlineKeyboardButton("✅ Chốt lời", callback_data=f"win_{signal_id}"),
                    types.InlineKeyboardButton("❌ Chốt lỗ", callback_data=f"loss_{signal_id}")
                )
            
            # Send message
            bot.send_message(uid, signal_text, reply_markup=markup, parse_mode="HTML")
            
            # Track sent (thread-safe)
            with data_lock:
                for sig in recent_signals.get("signals", []):
                    if sig["id"] == signal_id:
                        sig.setdefault("sent_to", []).append(uid)
                        break
            
            sent_count += 1
            
        except Exception as e:
            logger.error(f"❌ Error sending signal to {uid}: {e}")
    
    # Save after all sends (thread-safe)
    with data_lock:
        save_json("recent_signals.json", recent_signals)
    
    logger.info(f"📤 Signal sent to {sent_count} users")

# =============================================================================
# TELEGRAM BOT HANDLERS
# =============================================================================

@bot.message_handler(commands=["start"])
def start(message):
    """Handle /start command"""
    uid = str(message.from_user.id)
    name = message.from_user.first_name or "User"
    
    # Auto-subscribe new users (thread-safe)
    with data_lock:
        if uid not in users_data.get("users", {}):
            users_data.setdefault("users", {})[uid] = {
                "name": name,
                "chat_id": uid,
                "subscribed": True,
                "watchlist": [],
                "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            }
            save_json("users.json", users_data)
            logger.info(f"✅ New user registered: {name} ({uid})")
    
    welcome = f"""
👋 <b>Chào mừng {name}!</b>

🚀 <b>AI Tín hiệu Trading Futures 2025</b>
Hệ thống phân tích Smart Money Concepts với 16 chiến lược chuyên nghiệp.

<b>📋 Các lệnh:</b>
/start - Bắt đầu (đăng ký nhận tín hiệu)
/stop - Tạm dừng nhận tín hiệu
/watch BTCUSDT,ETHUSDT - Chỉ nhận tín hiệu coin này
/unwatch BTCUSDT - Bỏ theo dõi coin
/status - Xem trạng thái của bạn

<b>⚡ Quy tắc vàng:</b>
• Risk tối đa 0.5-1% mỗi lệnh
• Luôn đặt Stop Loss
• Không FOMO, không revenge trade
• Quản lý tâm lý và vốn nghiêm ngặt

<b>📊 16 Combos được phân tích:</b>
✓ FVG + Order Block + Liquidity
✓ MACD Divergence + Smart Money
✓ Volume Profile + Price Action
✓ RSI Extreme Reversal
... và nhiều hơn nữa!

<b>💬 Liên hệ Admin:</b> @HOANGDUNGG789

<i>Chúc bạn trading thành công! 📈🚀</i>
"""
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("📞 Liên hệ Admin", url="https://t.me/HOANGDUNGG789"))
    
    bot.send_message(message.chat.id, welcome, reply_markup=markup, parse_mode="HTML")

@bot.message_handler(commands=["stop"])
def stop_signals(message):
    """Unsubscribe from signals"""
    uid = str(message.from_user.id)
    
    with data_lock:
        if uid in users_data.get("users", {}):
            users_data["users"][uid]["subscribed"] = False
            save_json("users.json", users_data)
            bot.reply_to(message, "✅ Đã tạm dừng nhận tín hiệu. Gõ /start để bật lại.")
        else:
            bot.reply_to(message, "Bạn chưa đăng ký. Gõ /start để bắt đầu.")

@bot.message_handler(commands=["watch"])
def watch_coins(message):
    """Add coins to watchlist"""
    uid = str(message.from_user.id)
    
    try:
        coins = message.text.split()[1].upper().split(",")
        coins = [c.strip() for c in coins if c.strip()]
        
        with data_lock:
            if uid not in users_data.get("users", {}):
                bot.reply_to(message, "Vui lòng gõ /start trước.")
                return
            
            users_data["users"][uid]["watchlist"] = coins
            save_json("users.json", users_data)
        
        bot.reply_to(message, f"✅ Đã cập nhật watchlist: {', '.join(coins)}\n\nBạn sẽ chỉ nhận tín hiệu từ các coin này.")
        
    except IndexError:
        bot.reply_to(message, "❌ Sử dụng: /watch BTCUSDT,ETHUSDT,SOLUSDT")

@bot.message_handler(commands=["unwatch"])
def unwatch_coins(message):
    """Remove coins from watchlist"""
    uid = str(message.from_user.id)
    
    try:
        coins = message.text.split()[1].upper().split(",")
        coins = [c.strip() for c in coins if c.strip()]
        
        with data_lock:
            if uid not in users_data.get("users", {}):
                bot.reply_to(message, "Vui lòng gõ /start trước.")
                return
            
            current_watchlist = users_data["users"][uid].get("watchlist", [])
            new_watchlist = [c for c in current_watchlist if c not in coins]
            
            users_data["users"][uid]["watchlist"] = new_watchlist
            save_json("users.json", users_data)
        
        if new_watchlist:
            bot.reply_to(message, f"✅ Watchlist mới: {', '.join(new_watchlist)}")
        else:
            bot.reply_to(message, "✅ Đã xóa toàn bộ watchlist. Bạn sẽ nhận tín hiệu từ tất cả coin.")
        
    except IndexError:
        bot.reply_to(message, "❌ Sử dụng: /unwatch BTCUSDT")

@bot.message_handler(commands=["status"])
def status(message):
    """Show user status"""
    uid = str(message.from_user.id)
    
    with data_lock:
        if uid not in users_data.get("users", {}):
            bot.reply_to(message, "Bạn chưa đăng ký. Gõ /start để bắt đầu.")
            return
        
        user = users_data["users"][uid].copy()
    
    subscribed = user.get("subscribed", True)
    watchlist = user.get("watchlist", [])
    created = user.get("created_at", "N/A")
    
    status_text = f"""
<b>📊 Trạng thái của bạn:</b>

<b>Tên:</b> {user.get('name', 'N/A')}
<b>User ID:</b> <code>{uid}</code>
<b>Đăng ký:</b> {created}
<b>Trạng thái:</b> {'✅ Đang nhận tín hiệu' if subscribed else '⏸ Đã tạm dừng'}
<b>Watchlist:</b> {', '.join(watchlist) if watchlist else 'Tất cả coins'}

<b>Tổng tín hiệu hôm nay:</b> {stats['last_signal_count']}
<b>Lần quét cuối:</b> {stats['last_scan_time'].strftime('%H:%M:%S UTC') if stats['last_scan_time'] else 'Chưa quét'}
"""
    
    bot.send_message(message.chat.id, status_text, parse_mode="HTML")

@bot.message_handler(commands=["broadcast"])
def broadcast(message):
    """Admin broadcast message"""
    if ADMIN_ID is None or message.from_user.id != ADMIN_ID:
        return
    
    try:
        msg = message.text.split(None, 1)[1]
        count = 0
        
        with data_lock:
            users_copy = users_data.get("users", {}).copy()
        
        for uid, user_data in users_copy.items():
            if user_data.get("subscribed", True):
                try:
                    bot.send_message(uid, msg, parse_mode="HTML")
                    count += 1
                except Exception as e:
                    logger.error(f"Broadcast error to {uid}: {e}")
        
        bot.reply_to(message, f"✅ Đã gửi broadcast đến {count} users.")
        
    except IndexError:
        bot.reply_to(message, "❌ Sử dụng: /broadcast <nội dung>")

@bot.message_handler(commands=["summary"])
def summary(message):
    """Admin summary"""
    if ADMIN_ID is None or message.from_user.id != ADMIN_ID:
        return
    
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    
    with data_lock:
        recent_results = [
            r for r in results_data.get("results", [])
            if datetime.strptime(r["time"], "%Y-%m-%d").replace(tzinfo=timezone.utc) > week_ago
        ]
    
    win = len([r for r in recent_results if "lời" in r.get("result", "")])
    loss = len([r for r in recent_results if "lỗ" in r.get("result", "")])
    total = win + loss
    wr = (win / total * 100) if total > 0 else 0
    
    summary_text = f"""
<b>📊 Thống kê 7 ngày qua:</b>

✅ <b>Thắng:</b> {win}
❌ <b>Thua:</b> {loss}
📈 <b>Tổng:</b> {total}
🎯 <b>Win rate:</b> {wr:.1f}%

<b>Tổng tín hiệu gửi:</b> {stats['total_signals_sent']}
<b>Uptime:</b> {(datetime.now(timezone.utc) - stats['uptime_start']).days} ngày
"""
    
    bot.send_message(message.chat.id, summary_text, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    """Handle inline button callbacks"""
    if ADMIN_ID is None or call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Chỉ admin mới có quyền này.")
        return
    
    try:
        action, sig_id = call.data.split("_", 1)
        result = "✅ Chốt lời" if action == "win" else "❌ Chốt lỗ"
        
        # Update message
        try:
            bot.edit_message_text(
                call.message.text + f"\n\n<b>{result}</b> (Admin đã đóng lệnh)",
                call.message.chat.id,
                call.message.message_id,
                parse_mode="HTML"
            )
        except:
            pass
        
        # Save result (thread-safe)
        with data_lock:
            results_data.setdefault("results", []).append({
                "id": sig_id,
                "result": result,
                "time": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "admin_user": str(call.from_user.id)
            })
            save_json("results.json", results_data)
        
        # Notify users
        coin = sig_id.split("_")[0]
        with data_lock:
            signals_copy = recent_signals.get("signals", []).copy()
        
        for sig in signals_copy:
            if sig["id"] == sig_id:
                for uid in sig.get("sent_to", []):
                    if str(uid) != str(ADMIN_ID):
                        try:
                            bot.send_message(
                                uid,
                                f"<b>🔔 Cập nhật tín hiệu {coin}:</b>\n{result}",
                                parse_mode="HTML"
                            )
                        except:
                            pass
        
        bot.answer_callback_query(call.id, f"Đã ghi nhận: {result}")
        
    except Exception as e:
        logger.error(f"Callback error: {e}")
        bot.answer_callback_query(call.id, "Có lỗi xảy ra.")

# =============================================================================
# FLASK ROUTES (HEALTH CHECK)
# =============================================================================

@app.route('/')
def home():
    """Health check endpoint"""
    return jsonify({
        "status": "alive",
        "service": "Trading Bot v2.0",
        "uptime_seconds": (datetime.now(timezone.utc) - stats["uptime_start"]).total_seconds(),
        "last_scan": stats["last_scan_time"].strftime('%Y-%m-%d %H:%M:%S UTC') if stats["last_scan_time"] else None,
        "last_signal_count": stats["last_signal_count"],
        "total_signals": stats["total_signals_sent"]
    })

@app.route('/health')
def health():
    """Detailed health check"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'),
        "stats": {
            "last_scan": stats["last_scan_time"].strftime('%Y-%m-%d %H:%M:%S UTC') if stats["last_scan_time"] else None,
            "last_signal_count": stats["last_signal_count"],
            "total_signals_sent": stats["total_signals_sent"],
            "uptime_days": (datetime.now(timezone.utc) - stats["uptime_start"]).days
        },
        "config": {
            "interval": INTERVAL,
            "coins": len(COINS),
            "scan_interval_min": SCAN_INTERVAL_MINUTES,
            "combos": 16
        }
    })

@app.route('/metrics')
def metrics():
    """Metrics endpoint"""
    with data_lock:
        total_users = len(users_data.get("users", {}))
        active_users = sum(1 for u in users_data.get("users", {}).values() if u.get("subscribed", True))
        recent_signals_count = len(recent_signals.get("signals", []))
        results_count = len(results_data.get("results", []))
    
    return jsonify({
        "total_users": total_users,
        "active_users": active_users,
        "total_signals_sent": stats["total_signals_sent"],
        "recent_signals": recent_signals_count,
        "results_count": results_count
    })

# =============================================================================
# MAIN EXECUTION
# =============================================================================

def run_bot():
    """Run bot polling in separate thread"""
    logger.info("🤖 Bot polling started...")
    try:
        bot.infinity_polling(timeout=60, long_polling_timeout=60)
    except Exception as e:
        logger.error(f"❌ Bot polling error: {e}")

if __name__ == "__main__":
    logger.info("=" * 80)
    logger.info("🚀 PROFESSIONAL TRADING BOT v2.0 - STARTING")
    logger.info("=" * 80)
    logger.info(f"✅ Token: {TOKEN[:10]}...")
    logger.info(f"✅ Admin ID: {ADMIN_ID}")
    logger.info(f"✅ Interval: {INTERVAL}")
    logger.info(f"✅ Coins: {len(COINS)}")
    logger.info(f"✅ Scan Interval: {SCAN_INTERVAL_MINUTES} minutes")
    logger.info("=" * 80)
    
    # Start bot polling thread
    polling_thread = threading.Thread(target=run_bot, daemon=True)
    polling_thread.start()
    logger.info("✅ Bot polling thread started")
    
    # Start scheduler
    scheduler = BackgroundScheduler()
    scheduler.add_job(scan, 'interval', minutes=SCAN_INTERVAL_MINUTES)
    scheduler.start()
    logger.info(f"✅ Scheduler started (interval: {SCAN_INTERVAL_MINUTES} minutes)")
    
    # Run initial scan
    logger.info("🔍 Running initial scan...")
    try:
        scan()
    except Exception as e:
        logger.error(f"❌ Initial scan error: {e}")
    
    # Start Flask server
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"🌐 Starting Flask server on port {port}...")
    
    try:
        app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        logger.info("⚠️ Shutting down gracefully...")
        scheduler.shutdown()
    
    logger.info("Bot stopped.")
