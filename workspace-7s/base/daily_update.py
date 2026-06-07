"""
Daily update base class for workspace-7s.

Purpose: Incrementally update existing 3_processed/{symbol}.csv files
with new daily data via APIs.

Key Features:
- ETF-first strategy with optional INDEX fallback
- Anti-ban mechanisms (random delays, exponential backoff)
- Intraday filtering (don't use incomplete trading day data)
- Region-aware API selection
- Persistent file logging to logs/{region}/{date}.log
"""

from abc import ABC, abstractmethod
from typing import Dict, Optional
from datetime import datetime, time as dt_time
import logging
import os
import json
import pandas as pd
import time
import random


class DailyUpdateBase(ABC):
    """
    Base class for daily update services.

    Purpose: Incrementally update existing 3_processed/{symbol}.csv files
    with new daily data via APIs.
    """

    def __init__(self, region: str, base_path: Optional[str] = None):
        """
        Initialize daily update service.

        Args:
            region: 'CN' or 'US'
            base_path: Optional base path. Defaults to knowledge/{region}/ relative to CWD
                       (workspace root in production, or an explicit dev path during local testing)
        """
        self.region = region
        self.base_path = base_path or f"knowledge/{region}"

        # Standard pipeline directories
        self.raw_dir = os.path.join(self.base_path, "1_raw")
        self.staged_dir = os.path.join(self.base_path, "2_staged")
        self.processed_dir = os.path.join(self.base_path, "3_processed")
        self.archive_dir = os.path.join(self.base_path, "4_archive")
        self.metadata_file = os.path.join(self.base_path, "metadata.json")
        self.logs_dir = os.path.join(self.base_path, "logs")

        # Initialize directories and logging
        self._init_directories()
        self.logger = self._setup_file_logging()

    def _init_directories(self):
        """Ensure all required directories exist."""
        for directory in [self.raw_dir, self.staged_dir, self.processed_dir, self.archive_dir, self.logs_dir]:
            os.makedirs(directory, exist_ok=True)

    def _setup_file_logging(self) -> logging.Logger:
        """Setup file and console logging for daily updates."""
        logger = logging.getLogger(f"7s.daily_update.{self.region}")
        logger.setLevel(logging.DEBUG)

        # Clear existing handlers to avoid duplicates
        logger.handlers = []

        # File handler (logs/{region}/daily_update_YYYY-MM-DD.log)
        log_file = os.path.join(self.logs_dir, f"daily_update_{datetime.now().strftime('%Y-%m-%d')}.log")
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)

        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)

        # Formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)

        logger.addHandler(file_handler)
        logger.addHandler(console_handler)

        return logger

    def _should_skip_today_data(self) -> bool:
        """Check if today's data is incomplete (before 4:10 PM)."""
        now = datetime.now()
        cutoff = dt_time(16, 10)
        return now.time() < cutoff

    def _filter_intraday_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """Remove today's data if we're still in trading hours."""
        if df.empty or 'date' not in df.columns:
            return df
        today = datetime.now().date()
        if not df.empty and df['date'].iloc[-1].date() == today and self._should_skip_today_data():
            df = df[df['date'].dt.date < today]
        return df

    def _calculate_incremental_returns(self,
                                        df_prices: pd.DataFrame,
                                        last_date: pd.Timestamp,
                                        last_return: float) -> pd.DataFrame:
        """Calculate cumulative returns from percentage changes."""
        if df_prices.empty:
            return pd.DataFrame()

        last_date_pd = pd.to_datetime(last_date)

        df_with_baseline = df_prices.copy()
        if 'price' in df_with_baseline.columns and 'pct_chg' not in df_with_baseline.columns:
            df_with_baseline['pct_chg'] = df_with_baseline['price'].pct_change().fillna(0)

        df_new = df_with_baseline[df_with_baseline['date'] > last_date_pd].copy()

        if df_new.empty:
            return pd.DataFrame()

        result_rows = []
        current_return = last_return

        for _, row in df_new.iterrows():
            pct_chg = row.get('pct_chg', 0)
            current_return = current_return * (1 + pct_chg)
            entry = {
                'date': row['date'],
                'total_return': current_return,
                'close': row.get('close'),  # preserve actual close price
            }
            # Preserve volume if present (Sina source, dropped by CSI TR path)
            if 'volume' in df_prices.columns:
                vol_row = df_prices[df_prices['date'] == row['date']]
                if not vol_row.empty and 'volume' in vol_row.columns:
                    entry['volume'] = int(vol_row['volume'].iloc[0]) if pd.notna(vol_row['volume'].iloc[0]) else 0
            result_rows.append(entry)

        return pd.DataFrame(result_rows)

    def _apply_anti_ban_delay(self, attempt: int = 0):
        """Anti-ban mechanism with exponential backoff."""
        if attempt == 0:
            sleep_time = random.uniform(3, 7)
            time.sleep(sleep_time)
        else:
            sleep_time = attempt * 5
            print(f"  ⏳ [Retry delay] Waiting {sleep_time}s before retry...")
            time.sleep(sleep_time)

    def _bootstrap_history(self, symbol: str, asset_info: Dict) -> pd.DataFrame:
        """Seed a brand-new processed CSV using API history."""
        from datetime import timedelta

        lookback_days = int(asset_info.get('bootstrap_lookback_days', 3650))
        bootstrap_start = asset_info.get('bootstrap_start_date')
        if not bootstrap_start:
            bootstrap_start = (datetime.now() - timedelta(days=lookback_days)).strftime('%Y-%m-%d')

        bootstrap_info = dict(asset_info)
        bootstrap_info['bootstrap_mode'] = True
        df_prices = self.fetch_incremental_data(symbol, bootstrap_info, bootstrap_start)
        if df_prices.empty:
            return pd.DataFrame()

        df = df_prices.copy()
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').drop_duplicates(subset=['date'], keep='last').reset_index(drop=True)

        if 'price' in df.columns:
            prices = pd.to_numeric(df['price'], errors='coerce')
            df = df.assign(price=prices).dropna(subset=['price'])
            if df.empty:
                return pd.DataFrame()
            first_price = float(df['price'].iloc[0])
            if first_price <= 0:
                return pd.DataFrame()
            df['total_return'] = df['price'] / first_price
        elif 'pct_chg' in df.columns:
            rows = []
            current_return = 1.0
            for _, row in df.iterrows():
                current_return = current_return * (1 + float(row.get('pct_chg', 0) or 0))
                rows.append({'date': row['date'], 'total_return': current_return})
            df = pd.DataFrame(rows)
        else:
            return pd.DataFrame()

        # Include close if present (for Position calculation)
        cols = ['date', 'total_return']
        if 'close' in df.columns:
            cols.append('close')
        if 'volume' in df.columns:
            cols.append('volume')
        return df[cols].dropna(subset=['date', 'total_return']).reset_index(drop=True)

    def _write_initial_csv(self, symbol: str, df_initial: pd.DataFrame) -> bool:
        """Create the first processed CSV for a symbol from bootstrapped history."""
        if df_initial.empty:
            return False
        csv_path = os.path.join(self.processed_dir, f"{symbol}.csv")
        try:
            df_out = df_initial.copy()
            df_out['date'] = pd.to_datetime(df_out['date']).dt.strftime('%Y-%m-%d')
            df_out['total_return'] = pd.to_numeric(df_out['total_return'], errors='coerce').round(4)
            # Write close column if present (actual price for Position calculation)
            if 'close' in df_out.columns:
                df_out['close'] = pd.to_numeric(df_out['close'], errors='coerce').round(4)
            # Write volume column if present
            if 'volume' in df_out.columns:
                df_out['volume'] = pd.to_numeric(df_out['volume'], errors='coerce').fillna(0).astype(int)
            df_out = df_out.dropna().drop_duplicates(subset=['date'], keep='last').sort_values('date')
            df_out.to_csv(csv_path, index=False)
            return True
        except Exception as e:
            print(f"  ❌ [Bootstrap CSV Error] Failed to create initial CSV: {str(e)[:100]}")
            return False

    def _append_to_csv(self, symbol: str, df_new: pd.DataFrame) -> bool:
        """Append new data to existing CSV in 3_processed/."""
        if df_new.empty:
            return False
        csv_path = os.path.join(self.processed_dir, f"{symbol}.csv")
        if not os.path.exists(csv_path):
            print(f"  ⚠️ [Skip] CSV file not found: {csv_path}")
            return False
        try:
            df_existing = pd.read_csv(csv_path)
            df_existing['date'] = pd.to_datetime(df_existing['date'])
            df_final = pd.concat([df_existing, df_new], ignore_index=True)
            df_final = df_final.drop_duplicates(subset=['date'], keep='last')
            df_final = df_final.sort_values('date').reset_index(drop=True)
            df_final['date'] = df_final['date'].dt.strftime('%Y-%m-%d')
            df_final['total_return'] = df_final['total_return'].astype(float).round(4)
            # Write close column if present (actual price for Position calculation)
            if 'close' in df_final.columns:
                df_final['close'] = pd.to_numeric(df_final['close'], errors='coerce').round(4)
            # Write volume column if present
            if 'volume' in df_final.columns:
                df_final['volume'] = pd.to_numeric(df_final['volume'], errors='coerce').fillna(0).astype(int)
            df_final.to_csv(csv_path, index=False)
            return True
        except Exception as e:
            print(f"  ❌ [CSV Error] Failed to append data: {str(e)[:100]}")
            return False

    def _log_update(self, symbol: str, new_rows: int, last_return: float, status: str):
        """Log daily update metadata."""
        try:
            metadata = {}
            if os.path.exists(self.metadata_file):
                with open(self.metadata_file) as f:
                    metadata = json.load(f)
            daily = metadata.setdefault("daily_updates", [])
            daily.append({
                'timestamp': datetime.now().isoformat(),
                'symbol': symbol,
                'new_rows': new_rows,
                'last_return': round(last_return, 4),
                'status': status,
            })
            with open(self.metadata_file, 'w') as f:
                json.dump(metadata, f, indent=2)
        except Exception:
            pass

    def _pre_execute_hook(self, asset_db: Dict) -> None:
        """Hook called once before the main asset-processing loop. Subclasses may override."""
        pass

    @abstractmethod
    def fetch_incremental_data(self, symbol: str, asset_info: Dict, last_date: str) -> pd.DataFrame:
        """
        Fetch incremental price data from API.

        Must return DataFrame with columns: ['date', 'price'] or ['date', 'pct_chg']
        """
        pass

    def execute(self, asset_db: Optional[Dict] = None, **kwargs) -> Dict:
        """Execute daily update pipeline for all assets."""
        if not asset_db:
            asset_db = {}

        result = {
            'status': 'unknown',
            'updated_count': 0,
            'skipped_count': 0,
            'failed_count': 0,
            'assets_updated': [],
            'assets_skipped': [],
            'errors': []
        }

        if not asset_db:
            result['status'] = 'success'
            result['errors'].append('No assets in database')
            self.logger.info("No assets in database, skipping update")
            return result

        print()
        print("=" * 80)
        print(f"📈 Daily Update Pipeline - {self.region} Region")
        print("=" * 80)
        print()

        self.logger.info("=" * 80)
        self.logger.info(f"📈 Daily Update Pipeline - {self.region} Region")
        self.logger.info("=" * 80)
        self.logger.info(f"Execution started at {datetime.now().isoformat()}")

        try:
            self._pre_execute_hook(asset_db)
            for symbol, asset_info in asset_db.items():
                name = asset_info.get('name', symbol)
                csv_path = os.path.join(self.processed_dir, f"{symbol}.csv")

                print(f"⚡ Checking [{name}] ({symbol})...")
                self.logger.info(f"⚡ Checking [{name}] ({symbol})...")

                # 1. Check if CSV exists
                if not os.path.exists(csv_path):
                    temp_mode = str(os.getenv('SEVENS_TEMP_ASSET_MODE', '')).strip().lower()
                    allow_bootstrap = bool(kwargs.get('bootstrap_missing')) or temp_mode in {'replace', 'merge', 'temp'}

                    if not allow_bootstrap:
                        print(f"  ⚠️ [Skip] CSV not found (waiting for calibration)")
                        self.logger.warning(f"  ⚠️ [Skip] CSV not found for {symbol} (waiting for calibration)")
                        result['skipped_count'] += 1
                        result['assets_skipped'].append(symbol)
                        continue

                    print(f"  🚀 [Bootstrap] CSV not found, seeding initial history...")
                    self.logger.info(f"  🚀 [Bootstrap] CSV not found for {symbol}, seeding initial history")

                    try:
                        df_initial = self._bootstrap_history(symbol, asset_info)
                        if df_initial.empty:
                            print(f"  ⚠️ [Skip] Bootstrap returned no data")
                            self.logger.warning(f"  ⚠️ [Skip] Bootstrap returned no data for {symbol}")
                            result['skipped_count'] += 1
                            result['assets_skipped'].append(symbol)
                            continue

                        if self._write_initial_csv(symbol, df_initial):
                            new_return = df_initial['total_return'].iloc[-1]
                            msg = f"  ✅ Bootstrapped! {len(df_initial)} rows, latest return: {new_return:.4f}"
                            print(msg)
                            self.logger.info(msg)
                            self._log_update(symbol, len(df_initial), new_return, 'bootstrapped')
                            result['updated_count'] += 1
                            result['assets_updated'].append({
                                'symbol': symbol,
                                'new_rows': len(df_initial),
                                'latest_return': new_return,
                                'bootstrapped': True,
                            })
                        else:
                            msg = f"  ❌ [Failed to create initial CSV]"
                            print(msg)
                            self.logger.error(msg)
                            result['failed_count'] += 1
                            result['errors'].append(f"{symbol}: Failed to create initial CSV")
                        continue

                    except Exception as e:
                        msg = f"  ❌ [Bootstrap Error] {str(e)[:100]}"
                        print(msg)
                        self.logger.exception(f"Bootstrap failed for {symbol}: {str(e)}")
                        result['failed_count'] += 1
                        result['errors'].append(f"{symbol}: bootstrap {str(e)[:80]}")
                        continue

                try:
                    df_existing = pd.read_csv(csv_path)
                    df_existing['date'] = pd.to_datetime(df_existing['date'])

                    if df_existing.empty:
                        print(f"  ⚠️ [Skip] CSV is empty")
                        self.logger.warning(f"  ⚠️ [Skip] CSV is empty for {symbol}")
                        result['skipped_count'] += 1
                        result['assets_skipped'].append(symbol)
                        continue

                    last_date = df_existing['date'].iloc[-1]
                    last_return = df_existing['total_return'].iloc[-1]

                    df_prices = self.fetch_incremental_data(symbol, asset_info, str(last_date.date()))

                    if df_prices.empty:
                        print(f"  💤 [No new data] (as of {last_date.date()})")
                        self.logger.debug(f"  💤 [No new data] for {symbol} (as of {last_date.date()})")
                        result['skipped_count'] += 1
                        result['assets_skipped'].append(symbol)
                        continue

                    df_new = self._calculate_incremental_returns(df_prices, last_date, last_return)

                    if df_new.empty:
                        print(f"  💤 [No new data after date]")
                        self.logger.debug(f"  💤 [No new data after date] for {symbol}")
                        result['skipped_count'] += 1
                        result['assets_skipped'].append(symbol)
                        continue

                    if self._append_to_csv(symbol, df_new):
                        new_return = df_new['total_return'].iloc[-1]
                        msg = f"  ✅ Updated! {len(df_new)} new rows, latest return: {new_return:.4f}"
                        print(msg)
                        self.logger.info(msg)
                        self._log_update(symbol, len(df_new), new_return, 'success')
                        result['updated_count'] += 1
                        result['assets_updated'].append({
                            'symbol': symbol,
                            'new_rows': len(df_new),
                            'latest_return': new_return
                        })
                    else:
                        msg = f"  ❌ [Failed to append]"
                        print(msg)
                        self.logger.error(msg)
                        result['failed_count'] += 1
                        result['errors'].append(f"{symbol}: Failed to append data")
                        self._log_update(symbol, 0, last_return, 'failed')

                except Exception as e:
                    msg = f"  ❌ [Error] {str(e)[:100]}"
                    print(msg)
                    self.logger.exception(f"Exception while processing {symbol}: {str(e)}")
                    result['failed_count'] += 1
                    result['errors'].append(f"{symbol}: {str(e)[:80]}")

            # Determine overall status
            total = result['updated_count'] + result['skipped_count'] + result['failed_count']
            if total == 0:
                result['status'] = 'success'
            elif result['failed_count'] == 0:
                result['status'] = 'success'
            elif result['updated_count'] > 0:
                result['status'] = 'partial'
            else:
                result['status'] = 'failed'

        except Exception as e:
            result['status'] = 'failed'
            result['errors'].append(f"Pipeline error: {str(e)[:100]}")

        print()
        print("=" * 80)
        summary_msg = f"Summary: Updated {result['updated_count']} | Skipped {result['skipped_count']} | Failed {result['failed_count']}"
        print(summary_msg)
        print("=" * 80)
        self.logger.info(summary_msg)
        self.logger.info("=" * 80)
        self.logger.info(f"Execution completed at {datetime.now().isoformat()} with status: {result['status']}")

        return result
