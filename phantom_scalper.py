import asyncio
import os
import threading
import aiohttp
import json
import pandas as pd
import pandas_ta as ta
from datetime import datetime
from collections import deque
import logging

# --- Bot-Specific Configuration ---
BOT_NAME = "PhantomScalper_G"
STATE_FILE = f"state_{BOT_NAME}.json"
LOG_FILE = f"log_{BOT_NAME}.txt"
START_BALANCE = 1000.0
INVESTMENT_PERCENT_PER_TRADE = 15.0 

# --- Strategy Hyperparameters ---
SYMBOL = "BTCUSDT"
TIMEFRAME = "1m"
ZLEMA_PERIOD = 14
BBANDS_PERIOD = 20
BBANDS_STDDEV = 2.0
STOCHRSI_K = 14
STOCHRSI_D = 3
STOCHRSI_SMOOTH_K = 3 # This parameter is for the internal RSI calculation
STOCHRSI_OVERSOLD = 25
ATR_SL_MULTIPLIER = 1.5
ATR_TP_MULTIPLIER = 2.0

# --- Boilerplate Code ---
# --- Logging Setup ---
logger = logging.getLogger(__name__); logger.setLevel(logging.INFO)
file_handler = logging.FileHandler(LOG_FILE); file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(file_handler)
from flask import Flask
app = Flask(__name__)
@app.route('/')
def home(): return f"{BOT_NAME} is alive!"
def run_flask_app(): logging.getLogger('werkzeug').setLevel(logging.ERROR); app.run(host='0.0.0.0', port=8083)
from rich.console import Console; from rich.layout import Layout; from rich.live import Live; from rich.panel import Panel; from rich.table import Table; from rich.text import Text
console = Console(); system_log = deque(maxlen=10)

class BinanceAPI:
    def __init__(self, is_sandbox=True): self.base_url = "https://testnet.binance.vision/api/v3" if is_sandbox else "https://api.binance.com/api/v3"; self.session = None
    async def _get_session(self):
        if self.session is None or self.session.closed: self.session = aiohttp.ClientSession()
        return self.session
    async def get_candles(self, symbol, timeframe, retries=3, delay=5):
        endpoint = "/klines"; url = self.base_url + endpoint; params = {'symbol': symbol, 'interval': timeframe, 'limit': 100}
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
                    else: logger.warning(f"API Error: Status {response.status}. Retrying...")
            except asyncio.TimeoutError: logger.warning(f"API Timeout. Retrying...")
            except Exception as e: logger.error(f"Error fetching candles: {e}. Retrying...")
            await asyncio.sleep(delay * (attempt + 1))
        logger.error("Failed to fetch candles after multiple retries."); return None
    async def close(self):
        if self.session: await self.session.close()

class TradeManager:
    def __init__(self, start_balance): self.balance = start_balance; self.start_balance = start_balance; self.position_open = False; self.entry_price = 0.0; self.stop_loss_price = 0.0; self.take_profit_price = 0.0; self.qty = 0.0; self.wins = 0; self.losses = 0; self.trade_history = deque(maxlen=10)
    def to_dict(self): return {"balance": self.balance, "start_balance": self.start_balance, "position_open": self.position_open, "entry_price": self.entry_price, "stop_loss_price": self.stop_loss_price, "take_profit_price": self.take_profit_price, "qty": self.qty, "wins": self.wins, "losses": self.losses, "trade_history": list(self.trade_history)}
    @classmethod
    def from_dict(cls, data):
        manager = cls(data['start_balance']); manager.balance = data['balance']; manager.position_open = data['position_open']; manager.entry_price = data['entry_price']; manager.stop_loss_price = data['stop_loss_price']; manager.take_profit_price = data['take_profit_price']; manager.qty = data['qty']; manager.wins = data['wins']; manager.losses = data['losses']; manager.trade_history = deque(data.get('trade_history', []), maxlen=10)
        return manager

def save_state(manager):
    with open(STATE_FILE, 'w') as f: json.dump(manager.to_dict(), f, indent=4)
def load_state():
    if os.path.exists(STATE_FILE):
        msg = "Resuming from saved state."; logger.info(msg); system_log.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
        with open(STATE_FILE, 'r') as f: return TradeManager.from_dict(json.load(f))
    msg = "No saved state found. Starting fresh."; logger.info(msg); system_log.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
    return TradeManager(start_balance=START_BALANCE)

def generate_dashboard(trade_manager, price):
    layout = Layout(); layout.split(Layout(name="header", size=3), Layout(ratio=1, name="main"), Layout(size=12, name="footer")); layout["main"].split_row(Layout(name="left"), Layout(name="right"))
    header_text = Text(f"{BOT_NAME} Dashboard - {SYMBOL} ({TIMEFRAME})", style="bold magenta", justify="center"); layout["header"].update(Panel(header_text))
    total_pnl = trade_manager.balance - trade_manager.start_balance; total_pnl_pct = (total_pnl / trade_manager.start_balance) * 100 if trade_manager.start_balance else 0
    stats_table = Table(show_header=False, box=None); stats_table.add_column(); stats_table.add_column()
    stats_table.add_row("Balance", f"[bold cyan]${trade_manager.balance:.2f} USDT"); stats_table.add_row("Total PnL", Text(f"${total_pnl:+.2f} ({total_pnl_pct:+.2f}%)", style="green" if total_pnl >= 0 else "red")); stats_table.add_row("Wins / Losses", f"[green]{trade_manager.wins}[/green] / [red]{trade_manager.losses}[/red]"); stats_table.add_row("Last Price", f"${price:.2f}"); layout["left"].update(Panel(stats_table, title="Live Stats", border_style="blue"))
    history_text = "\n".join(trade_manager.trade_history); layout["right"].update(Panel(Text(history_text), title="Trade History", border_style="blue"))
    if trade_manager.position_open:
        pos_table = Table(show_header=False, box=None, expand=True); current_value = trade_manager.qty * price; live_pnl = (price - trade_manager.entry_price) * trade_manager.qty; risk_amount = (trade_manager.entry_price - trade_manager.stop_loss_price) * trade_manager.qty
        pos_table.add_row(f"Entry: ${trade_manager.entry_price:.2f}", f"Qty: {trade_manager.qty:.6f}", f"Value: ${current_value:.2f}", f"Risk: ${risk_amount:.2f}"); pos_table.add_row(f"SL: ${trade_manager.stop_loss_price:.2f}", f"TP: ${trade_manager.take_profit_price:.2f}", "", Text(f"Live PnL: ${live_pnl:+.2f}", style="green" if live_pnl >= 0 else "red")); layout["footer"].update(Panel(pos_table, title="Open Position", border_style="yellow"))
    else: log_text = "\n".join(system_log); layout["footer"].update(Panel(Text(log_text), title="System Log", border_style="green"))
    return layout

# --- Strategy & Indicators ---
def add_indicators(df):
    df.ta.zlma(length=ZLEMA_PERIOD, append=True)
    df.ta.bbands(length=BBANDS_PERIOD, std=BBANDS_STDDEV, append=True)
    df.ta.stochrsi(k=STOCHRSI_K, d=STOCHRSI_D, append=True)
    df.ta.atr(append=True)
    return df

def check_buy_signal(df):
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    
    price_above_zlma = latest['close'] > latest[f'ZLMA_{ZLEMA_PERIOD}']
    
    avg_bbw = df[f'BBW_{BBANDS_PERIOD}_{BBANDS_STDDEV}'].rolling(window=50).mean().iloc[-1]
    market_is_active = latest[f'BBW_{BBANDS_PERIOD}_{BBANDS_STDDEV}'] > avg_bbw * 0.75

    stoch_col_name = f'STOCHRSIk_{STOCHRSI_K}_{STOCHRSI_D}'
    stoch_rsi_k = latest[stoch_col_name]
    prev_stoch_rsi_k = prev[stoch_col_name]
    stoch_cross_up = prev_stoch_rsi_k < STOCHRSI_OVERSOLD and stoch_rsi_k >= STOCHRSI_OVERSOLD

    if price_above_zlma and market_is_active and stoch_cross_up:
        msg = f"📈 Buy Signal Found! StochRSI crossed up from {STOCHRSI_OVERSOLD}"
        logger.info(msg); system_log.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
        return True
    return False

# --- Main Bot Loop ---
async def main():
    logger.info(f"🚀 {BOT_NAME} Bot starting up..."); api = BinanceAPI(is_sandbox=True); trade_manager = load_state()
    current_price = 0.0
    with Live(generate_dashboard(trade_manager, current_price), screen=True, redirect_stderr=False, refresh_per_second=4) as live:
        try:
            while True:
                candles_df = await api.get_candles(SYMBOL, TIMEFRAME)
                if candles_df is None or candles_df.empty: await asyncio.sleep(20); continue
                current_price = candles_df.iloc[-1]['close']; candles_df = add_indicators(candles_df)
                if not trade_manager.position_open:
                    stoch_col_name = f'STOCHRSIk_{STOCHRSI_K}_{STOCHRSI_D}'
                    # This check ensures the column exists before we try to use it
                    if stoch_col_name in candles_df.columns:
                        stoch_rsi_val = candles_df.iloc[-1][stoch_col_name]
                        msg = f"Searching... Price: ${current_price:<9.2f} | StochRSI: {stoch_rsi_val:.2f}"
                        system_log.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
                        if check_buy_signal(candles_df):
                            latest = candles_df.iloc[-1]; entry_price = latest['close']; investment_amount_usdt = trade_manager.balance * (INVESTMENT_PERCENT_PER_TRADE / 100)
                            if investment_amount_usdt < 10.0:
                                msg = f"⚠️ Investment of ${investment_amount_usdt:.2f} is below $10 minimum."
                                logger.warning(msg); system_log.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
                            else:
                                trade_manager.qty = round(investment_amount_usdt / entry_price, 6); atr = latest[f'ATRr_{14}']
                                trade_manager.stop_loss_price = entry_price - (atr * ATR_SL_MULTIPLIER)
                                trade_manager.take_profit_price = entry_price + (atr * ATR_TP_MULTIPLIER)
                                trade_manager.entry_price = entry_price; trade_manager.position_open = True
                                msg = f"✅ Position Opened: Qty={trade_manager.qty:.6f} @ ${entry_price:.2f}"
                                logger.info(msg); system_log.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"); save_state(trade_manager)
                    else: # Handle case where indicator isn't ready
                        msg = f"Searching... Price: ${current_price:<9.2f} | StochRSI: (calculating...)"
                        system_log.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
                else: # Check exit logic
                    exit_reason = None
                    if current_price <= trade_manager.stop_loss_price: exit_reason = "Stop-Loss"
                    elif current_price >= trade_manager.take_profit_price: exit_reason = "Take-Profit"
                    if exit_reason:
                        profit = (current_price - trade_manager.entry_price) * trade_manager.qty
                        pnl_pct = (profit / (trade_manager.qty * trade_manager.entry_price)) * 100 if trade_manager.qty > 0 else 0
                        trade_manager.balance += profit
                        if profit > 0: trade_manager.wins += 1; trade_history_msg = f"[bold green]WIN : +${profit:.2f} ({pnl_pct:.2f}%)"
                        else: trade_manager.losses += 1; trade_history_msg = f"[bold red]LOSS: ${profit:.2f} ({pnl_pct:.2f}%)"
                        trade_manager.trade_history.append(trade_history_msg)
                        msg = f"❌ Position Closed by {exit_reason}. PnL: ${profit:+.2f}."
                        logger.info(msg); system_log.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"); trade_manager._reset_position(); save_state(trade_manager)
                    else:
                        live_pnl = (current_price - trade_manager.entry_price) * trade_manager.qty
                        logger.info(f"Position Open. Price: ${current_price:<9.2f} | Live PnL: ${live_pnl:+.2f}")
                live.update(generate_dashboard(trade_manager, current_price))
                await asyncio.sleep(60)
        except asyncio.CancelledError: logger.info("Bot shutting down gracefully.")
        finally: await api.close()
def _reset_position(self): self.position_open=False; self.entry_price=0.0; self.stop_loss_price=0.0; self.take_profit_price=0.0; self.qty=0.0
TradeManager._reset_position = _reset_position
if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask_app); flask_thread.daemon = True; flask_thread.start()
    try: asyncio.run(main())
    except KeyboardInterrupt: logger.info("Bot stopped manually.")
