# combo_i_squeeze.py

import asyncio, os, threading, aiohttp, json, pandas as pd, pandas_ta as ta, logging
from datetime import datetime
from collections import deque

# --- Bot-Specific Configuration ---
BOT_NAME = "Squeeze_I"
STATE_FILE = f"state_{BOT_NAME}.json"
LOG_FILE = f"log_{BOT_NAME}.txt"
START_BALANCE = 1000.0
INVESTMENT_PERCENT_PER_TRADE = 10.0

# --- Strategy Hyperparameters ---
SYMBOL = "BTCUSDT"
TIMEFRAME_15M = "15m"
TIMEFRAME_1M = "1m"
BB_LENGTH = 20
BB_STDEV = 2
BB_SQUEEZE_THRESHOLD = 0.05
VOLUME_AVG_PERIOD = 50
VOLUME_SPIKE_FACTOR = 2
ADX_PERIOD = 14
ADX_THRESHOLD = 20
ATR_PERIOD = 14
ATR_MIN_PERCENT = 0.3
TP_PERCENT = 2.0
SL_PERCENT = 0.8

# --- Boilerplate Code (Omitted for brevity - same as others) ---
logger = logging.getLogger(__name__); logger.setLevel(logging.INFO)
file_handler = logging.FileHandler(LOG_FILE); file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(file_handler)
from flask import Flask
app = Flask(__name__)
@app.route('/')
def home(): return f"{BOT_NAME} is alive!"
def run_flask_app(): logging.getLogger('werkzeug').setLevel(logging.ERROR); app.run(host='0.0.0.0', port=8087) # New Port
from rich.console import Console; from rich.layout import Layout; from rich.live import Live; from rich.panel import Panel; from rich.table import Table; from rich.text import Text
console = Console(); system_log = deque(maxlen=10)
class BinanceAPI: # ... (Omitted for brevity - same as others)
    def __init__(self, is_sandbox=True): self.base_url = "https://testnet.binance.vision/api/v3" if is_sandbox else "https://api.binance.com/api/v3"; self.session = None
    async def _get_session(self):
        if self.session is None or self.session.closed: self.session = aiohttp.ClientSession()
        return self.session
    async def get_candles(self, symbol, timeframe, retries=3, delay=5):
        endpoint = "/klines"; url = self.base_url + endpoint; params = {'symbol': symbol, 'interval': timeframe, 'limit': 300}
        timeout = aiohttp.ClientTimeout(total=10)
        for attempt in range(retries):
            try:
                session = await self._get_session()
                async with session.get(url, params=params, timeout=timeout) as response:
                    if response.status == 200:
                        data = await response.json()
                        df = pd.DataFrame(data, columns=['time', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'])
                        for col in ['open', 'high', 'low', 'close', 'volume']: df[col] = pd.to_numeric(df[col])
                        df['time'] = pd.to_datetime(df['time'], unit='ms'); df.set_index('time', inplace=True)
                        return df
            except Exception: await asyncio.sleep(delay * (attempt + 1))
        return None
    async def close(self):
        if self.session: await self.session.close()
class TradeManager: # ... (Omitted for brevity - same as Combo B)
    def __init__(self, start_balance): self.balance = start_balance; self.start_balance = start_balance; self.position_open = False; self.position_side = None; self.entry_price = 0.0; self.stop_loss_price = 0.0; self.take_profit_price = 0.0; self.qty = 0.0; self.wins = 0; self.losses = 0; self.trade_history = deque(maxlen=10)
    def to_dict(self): return {"balance": self.balance, "start_balance": self.start_balance, "position_open": self.position_open, "position_side": self.position_side, "entry_price": self.entry_price, "stop_loss_price": self.stop_loss_price, "take_profit_price": self.take_profit_price, "qty": self.qty, "wins": self.wins, "losses": self.losses, "trade_history": list(self.trade_history)}
    @classmethod
    def from_dict(cls, data):
        manager = cls(data['start_balance']); manager.balance = data['balance']; manager.position_open = data['position_open']; manager.position_side = data.get('position_side'); manager.entry_price = data['entry_price']; manager.stop_loss_price = data['stop_loss_price']; manager.take_profit_price = data['take_profit_price']; manager.qty = data['qty']; manager.wins = data['wins']; manager.losses = data['losses']; manager.trade_history = deque(data.get('trade_history', []), maxlen=10)
        return manager
def save_state(manager):
    with open(STATE_FILE, 'w') as f: json.dump(manager.to_dict(), f, indent=4)
def load_state():
    if os.path.exists(STATE_FILE):
        msg = "Resuming from saved state."; logger.info(msg); system_log.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
        with open(STATE_FILE, 'r') as f: return TradeManager.from_dict(json.load(f))
    msg = "No saved state found. Starting fresh."; logger.info(msg); system_log.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
    return TradeManager(start_balance=START_BALANCE)
def generate_dashboard(trade_manager, price): # ... (Omitted for brevity - same as others)
    layout = Layout(); layout.split(Layout(name="header", size=3), Layout(ratio=1, name="main"), Layout(size=12, name="footer")); layout["main"].split_row(Layout(name="left"), Layout(name="right"))
    header_text = Text(f"{BOT_NAME} Dashboard - {SYMBOL} ({TIMEFRAME_1M})", style="bold magenta", justify="center"); layout["header"].update(Panel(header_text))
    total_pnl = trade_manager.balance - trade_manager.start_balance; total_pnl_pct = (total_pnl / trade_manager.start_balance) * 100 if trade_manager.start_balance else 0
    stats_table = Table(show_header=False, box=None); stats_table.add_column(); stats_table.add_column()
    stats_table.add_row("Balance", f"[bold cyan]${trade_manager.balance:.2f} USDT"); stats_table.add_row("Total PnL", Text(f"${total_pnl:+.2f} ({total_pnl_pct:+.2f}%)", style="green" if total_pnl >= 0 else "red")); stats_table.add_row("Wins / Losses", f"[green]{trade_manager.wins}[/green] / [red]{trade_manager.losses}[/red]"); stats_table.add_row("Last Price", f"${price:.2f}"); layout["left"].update(Panel(stats_table, title="Live Stats", border_style="blue"))
    history_text = "\n".join(trade_manager.trade_history); layout["right"].update(Panel(Text(history_text), title="Trade History", border_style="blue"))
    if trade_manager.position_open:
        pos_table = Table(show_header=False, box=None, expand=True); current_value = trade_manager.qty * price; 
        if trade_manager.position_side == 'long': live_pnl = (price - trade_manager.entry_price) * trade_manager.qty
        else: live_pnl = (trade_manager.entry_price - price) * trade_manager.qty
        pos_table.add_row(f"Side: {trade_manager.position_side.upper()}", f"Entry: ${trade_manager.entry_price:.2f}", f"Qty: {trade_manager.qty:.6f}", f"Value: ${current_value:.2f}")
        pos_table.add_row(f"SL: ${trade_manager.stop_loss_price:.2f}", f"TP: ${trade_manager.take_profit_price:.2f}", "", Text(f"Live PnL: ${live_pnl:+.2f}", style="green" if live_pnl >= 0 else "red")); layout["footer"].update(Panel(pos_table, title="Open Position", border_style="yellow"))
    else: log_text = "\n".join(system_log); layout["footer"].update(Panel(Text(log_text), title="System Log", border_style="green"))
    return layout
def _reset_position(self): self.position_open=False; self.position_side=None; self.entry_price=0.0; self.stop_loss_price=0.0; self.take_profit_price=0.0; self.qty=0.0
TradeManager._reset_position = _reset_position

# --- Strategy & Indicators ---
def add_indicators_15m(df):
    df.ta.bbands(length=BB_LENGTH, std=BB_STDEV, append=True)
    df.ta.adx(length=ADX_PERIOD, append=True)
    df['volume_avg'] = df['volume'].rolling(window=VOLUME_AVG_PERIOD).mean()
    return df

def add_indicators_1m(df):
    df.ta.bbands(length=BB_LENGTH, std=BB_STDEV, append=True)
    df.ta.atr(length=ATR_PERIOD, append=True)
    return df

async def check_15m_signal(api):
    df_15m = await api.get_candles(SYMBOL, TIMEFRAME_15M)
    if df_15m is None or df_15m.empty: return None
    df_15m = add_indicators_15m(df_15m)
    latest_15m = df_15m.iloc[-1]
    
    # 1. Bollinger Band Squeeze
    bandwidth = (latest_15m['BBU_20_2.0'] - latest_15m['BBL_20_2.0']) / latest_15m['BBM_20_2.0']
    is_squeezing = bandwidth < BB_SQUEEZE_THRESHOLD
    
    # 2. Volume Spike & ADX rising
    volume_confirms = latest_15m['volume'] > latest_15m['volume_avg'] * VOLUME_SPIKE_FACTOR
    adx_confirms = latest_15m[f'ADX_{ADX_PERIOD}'] > ADX_THRESHOLD
    
    if is_squeezing and volume_confirms and adx_confirms:
        # Check for breakout direction
        if latest_15m['close'] > latest_15m['BBU_20_2.0']: return "long"
        elif latest_15m['close'] < latest_15m['BBL_20_2.0']: return "short"
    return None

def check_1m_entry(df_1m, direction):
    latest_1m = df_1m.iloc[-1]
    atr_value = latest_1m[f'ATR_{ATR_PERIOD}']
    volatility_check = atr_value >= (ATR_MIN_PERCENT / 100) * latest_1m['close']
    
    if direction == "long" and latest_1m['close'] > latest_1m['BBU_20_2.0'] and volatility_check:
        return True
    elif direction == "short" and latest_1m['close'] < latest_1m['BBL_20_2.0'] and volatility_check:
        return True
    return False

# --- Main Bot Loop ---
async def main():
    logger.info(f"🚀 {BOT_NAME} Bot starting up..."); api = BinanceAPI(is_sandbox=True); trade_manager = load_state()
    current_price = 0.0; squeeze_direction = None
    with Live(generate_dashboard(trade_manager, current_price), screen=True, redirect_stderr=False, refresh_per_second=4) as live:
        try:
            while True:
                candles_df_1m = await api.get_candles(SYMBOL, TIMEFRAME_1M)
                candles_df_15m = await api.get_candles(SYMBOL, TIMEFRAME_15M)
                if candles_df_1m is None or candles_df_1m.empty or candles_df_15m is None or candles_df_15m.empty: await asyncio.sleep(5); continue
                current_price = candles_df_1m.iloc[-1]['close']; candles_df_1m = add_indicators_1m(candles_df_1m)
                
                # Check for 15m signal first
                if squeeze_direction is None:
                    squeeze_direction = await check_15m_signal(api)
                    if squeeze_direction:
                        msg = f"⏳ 15m Squeeze Breakout Detected! Direction: {squeeze_direction.upper()}"
                        logger.info(msg); system_log.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
                
                if not trade_manager.position_open and squeeze_direction:
                    entry_signal = check_1m_entry(candles_df_1m, squeeze_direction)
                    if entry_signal:
                        entry_price = current_price
                        investment_amount_usdt = trade_manager.balance * (INVESTMENT_PERCENT_PER_TRADE / 100)
                        if investment_amount_usdt > 10.0:
                            trade_manager.position_side = squeeze_direction; trade_manager.qty = round(investment_amount_usdt / entry_price, 6)
                            if squeeze_direction == "long":
                                trade_manager.stop_loss_price = entry_price * (1 - SL_PERCENT / 100)
                                trade_manager.take_profit_price = entry_price * (1 + TP_PERCENT / 100)
                            else:
                                trade_manager.stop_loss_price = entry_price * (1 + SL_PERCENT / 100)
                                trade_manager.take_profit_price = entry_price * (1 - TP_PERCENT / 100)
                            trade_manager.entry_price = entry_price; trade_manager.position_open = True
                            msg = f"✅ Position Opened: {squeeze_direction.upper()} Qty={trade_manager.qty:.6f} @ ${entry_price:.2f}"
                            logger.info(msg); system_log.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"); save_state(trade_manager)
                        squeeze_direction = None # Reset the 15m signal
                else: # Check Exit Logic
                    exit_reason = None
                    if trade_manager.position_open:
                        if trade_manager.position_side == 'long':
                            if current_price <= trade_manager.stop_loss_price: exit_reason = "Stop-Loss"
                            elif current_price >= trade_manager.take_profit_price: exit_reason = "Take-Profit"
                        elif trade_manager.position_side == 'short':
                            if current_price >= trade_manager.stop_loss_price: exit_reason = "Stop-Loss"
                            elif current_price <= trade_manager.take_profit_price: exit_reason = "Take-Profit"
                    if exit_reason:
                        profit = (current_price - trade_manager.entry_price) * trade_manager.qty if trade_manager.position_side == 'long' else (trade_manager.entry_price - current_price) * trade_manager.qty
                        pnl_pct = (profit / (trade_manager.qty * trade_manager.entry_price)) * 100 if trade_manager.qty > 0 else 0
                        trade_manager.balance += profit
                        if profit > 0: trade_manager.wins += 1; trade_history_msg = f"[bold green]WIN : +${profit:.2f} ({pnl_pct:.2f}%)"
                        else: trade_manager.losses += 1; trade_history_msg = f"[bold red]LOSS: ${profit:.2f} ({pnl_pct:.2f}%)"
                        trade_manager.trade_history.append(trade_history_msg)
                        msg = f"❌ Position Closed by {exit_reason}. PnL: ${profit:+.2f}."
                        logger.info(msg); system_log.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"); trade_manager._reset_position(); save_state(trade_manager)
                live.update(generate_dashboard(trade_manager, current_price))
                await asyncio.sleep(15)
        except asyncio.CancelledError: logger.info("Bot shutting down gracefully.")
        finally: await api.close()

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask_app); flask_thread.daemon = True; flask_thread.start()
    try: asyncio.run(main())
    except KeyboardInterrupt: logger.info("Bot stopped manually.")
