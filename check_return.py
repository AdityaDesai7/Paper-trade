import pandas as pd
import numpy as np

# Load the features CSV
df = pd.read_csv(r'c:\Users\Aditya desai\Desktop\Trading Strategy\output\master_oil_features_20260320_222159.csv')

# Strategy-like pipeline:
feat = df.copy()
# Add rolling metrics (max lag is 20)
feat['WTI_LogRet_1d'] = np.log(feat['WTI_Close'] / feat['WTI_Close'].shift(1))
feat['RealizedVol_20d'] = feat['WTI_LogRet_1d'].rolling(20).std() * np.sqrt(252)

# Simulate dropping NaNs as done in strategy.py
clean_feat = feat.dropna()

print(f"Total CSV Rows: {len(df)}")
print(f"Rows surviving dropna(): {len(clean_feat)}")

initial_train_days = 500
if len(clean_feat) > initial_train_days:
    # First day with signal is index initial_train_days
    oos = clean_feat.iloc[initial_train_days:].copy()
    oos_start_date = oos.iloc[0]['Date']
    oos_start_price = oos.iloc[0]['WTI_Close']
    end_price = oos.iloc[-1]['WTI_Close']
    
    bnh_return = (end_price / oos_start_price) - 1
    
    print(f"OOS Start Date: {oos_start_date}")
    print(f"OOS Start Price: {oos_start_price:.2f}")
    print(f"End Price: {end_price:.2f}")
    print(f"Total BnH Return: {bnh_return*100:.2f}%")
else:
    print("Not enough rows for OOS")
