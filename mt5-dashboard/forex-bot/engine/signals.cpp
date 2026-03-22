#include <cmath>
#include <cstring>
#include <algorithm>
using namespace std;

// Forward declarations from indicators.cpp
extern "C" {
    void calc_ema(const double*, int, int, double*);
    void calc_rsi(const double*, int, int, double*);
    void calc_atr(const double*, const double*, const double*, int, int, double*);
    void calc_adx(const double*, const double*, const double*, int, int, double*, double*, double*);
    void calc_macd(const double*, int, int, int, int, double*, double*, double*);
    double atr_rolling_avg(const double*, int, int);
}

extern "C" {

// ── Signal output structure ───────────────────────────────────────────────
struct Signal {
    int    direction;        // 1=buy, -1=sell, 0=flat
    int    tss_score;        // 0–5 Trend Strength Score
    double rsi;
    double atr;
    double atr_avg;          // 20-bar ATR average
    double atr_ratio;        // atr / atr_avg
    double ema21;
    double ema50;
    double ema200;
    double adx;
    double di_plus;
    double di_minus;
    double macd_hist;
    double sl_distance;      // in price units (1.5x ATR)
    double tp1_distance;     // 1.5R
    double tp2_distance;     // 3.0R
    char   atr_zone[16];     // "low","normal","elevated","extreme"
    char   reason[256];
    int    checklist_score;  // 0–7 from entry checklist
};

// ── Divergence detection ──────────────────────────────────────────────────
// Returns: 1=regular_bullish, 2=regular_bearish,
//          3=hidden_bullish, 4=hidden_bearish, 0=none
int detect_divergence(const double* price, const double* rsi, int n, int lookback) {
    if (n < lookback + 2) return 0;
    int i = n - 1;
    int prev = i - lookback;

    bool price_hh = price[i] > price[prev];
    bool price_ll = price[i] < price[prev];
    bool rsi_hh   = rsi[i]   > rsi[prev];
    bool rsi_ll   = rsi[i]   < rsi[prev];

    if (price_hh && !rsi_hh)  return 2; // regular bearish
    if (price_ll && !rsi_ll)  return 1; // regular bullish
    if (!price_hh && rsi_hh)  return 4; // hidden bearish
    if (!price_ll && rsi_ll)  return 3; // hidden bullish
    return 0;
}

// ── ATR zone string ───────────────────────────────────────────────────────
void get_atr_zone(double ratio, char* out) {
    if      (ratio > 2.5) strncpy(out, "extreme",  15);
    else if (ratio > 1.5) strncpy(out, "elevated", 15);
    else if (ratio < 0.5) strncpy(out, "low",      15);
    else                  strncpy(out, "normal",   15);
}

// ── Entry checklist score (0–7) ───────────────────────────────────────────
int score_checklist(int direction, double ema50, double ema200,
                    double rsi, double macd_hist, double volume,
                    double vol_avg, double atr_ratio) {
    int score = 0;
    if (direction == 1) {
        if (ema50 > ema200)                         score++; // HTF bias
        if (rsi >= 30 && rsi <= 45)                 score++; // RSI zone
        if (macd_hist > 0)                          score++; // MACD
        if (volume > vol_avg * 1.2)                 score++; // Volume
        if (atr_ratio > 0.5 && atr_ratio < 2.5)    score++; // Volatility OK
        score += 2; // Zone + candle scored externally; add 2 base
    } else if (direction == -1) {
        if (ema50 < ema200)                         score++;
        if (rsi >= 55 && rsi <= 70)                 score++;
        if (macd_hist < 0)                          score++;
        if (volume > vol_avg * 1.2)                 score++;
        if (atr_ratio > 0.5 && atr_ratio < 2.5)    score++;
        score += 2;
    }
    return min(score, 7);
}

// ── Main signal evaluation ────────────────────────────────────────────────
Signal evaluate_signal(
    const double* close, const double* high,
    const double* low,   const double* volume,
    int n)
{
    Signal s;
    memset(&s, 0, sizeof(Signal));

    if (n < 210) {
        strncpy(s.reason, "Insufficient bars (need 210+)", 255);
        return s;
    }

    // Allocate indicator buffers
    double* ema21    = new double[n]();
    double* ema50    = new double[n]();
    double* ema200   = new double[n]();
    double* rsi_buf  = new double[n]();
    double* atr_buf  = new double[n]();
    double* adx_buf  = new double[n]();
    double* dip_buf  = new double[n]();
    double* dim_buf  = new double[n]();
    double* macd_l   = new double[n]();
    double* macd_sig = new double[n]();
    double* macd_h   = new double[n]();

    // Compute all indicators
    calc_ema(close, n, 21,  ema21);
    calc_ema(close, n, 50,  ema50);
    calc_ema(close, n, 200, ema200);
    calc_rsi(close, n, 14,  rsi_buf);
    calc_atr(high, low, close, n, 14, atr_buf);
    calc_adx(high, low, close, n, 14, adx_buf, dip_buf, dim_buf);
    calc_macd(close, n, 12, 26, 9, macd_l, macd_sig, macd_h);

    int i = n - 1;

    // Fill scalar outputs
    s.rsi      = rsi_buf[i];
    s.atr      = atr_buf[i];
    s.atr_avg  = atr_rolling_avg(atr_buf, n, 20);
    s.atr_ratio= (s.atr_avg > 0) ? s.atr / s.atr_avg : 1.0;
    s.ema21    = ema21[i];
    s.ema50    = ema50[i];
    s.ema200   = ema200[i];
    s.adx      = adx_buf[i];
    s.di_plus  = dip_buf[i];
    s.di_minus = dim_buf[i];
    s.macd_hist= macd_h[i];

    get_atr_zone(s.atr_ratio, s.atr_zone);

    // ── TSS scoring (max 5) ───────────────────────────────────────────────
    bool bull_stack = ema21[i] > ema50[i] && ema50[i] > ema200[i];
    bool bear_stack = ema21[i] < ema50[i] && ema50[i] < ema200[i];

    if (bull_stack || bear_stack)         s.tss_score++;   // EMA aligned
    if (s.adx > 25.0)                     s.tss_score++;   // ADX trending
    if (close[i] > ema200[max(0,i-50)])   s.tss_score++;   // Above weekly EMA200
    if ((bull_stack && s.macd_hist > 0) ||
        (bear_stack && s.macd_hist < 0))  s.tss_score++;   // MACD agrees
    double vol_avg = 0;
    for (int j = max(0, i-19); j <= i; j++) vol_avg += volume[j];
    vol_avg /= 20.0;
    if (volume[i] > vol_avg * 1.0)        s.tss_score++;   // Volume confirms

    // ── Direction decision ────────────────────────────────────────────────
    bool atr_ok = strcmp(s.atr_zone, "extreme") != 0;

    if (bull_stack && s.rsi >= 30 && s.rsi <= 45 &&
        s.tss_score >= 3 && atr_ok)
    {
        s.direction = 1;
    } else if (bear_stack && s.rsi >= 55 && s.rsi <= 70 &&
               s.tss_score >= 3 && atr_ok)
    {
        s.direction = -1;
    } else {
        s.direction = 0;
    }

    // ── SL / TP distances ─────────────────────────────────────────────────
    s.sl_distance  = s.atr * 1.5;
    s.tp1_distance = s.sl_distance * 1.5;
    s.tp2_distance = s.sl_distance * 3.0;

    // ── Checklist score ───────────────────────────────────────────────────
    s.checklist_score = score_checklist(
        s.direction, s.ema50, s.ema200,
        s.rsi, s.macd_hist, volume[i], vol_avg, s.atr_ratio);

    // ── Reason string ─────────────────────────────────────────────────────
    if (s.direction == 1)
        snprintf(s.reason, 255,
            "BUY | TSS=%d | RSI=%.1f | ADX=%.1f | ATR_zone=%s | MACD_hist=%.5f",
            s.tss_score, s.rsi, s.adx, s.atr_zone, s.macd_hist);
    else if (s.direction == -1)
        snprintf(s.reason, 255,
            "SELL | TSS=%d | RSI=%.1f | ADX=%.1f | ATR_zone=%s | MACD_hist=%.5f",
            s.tss_score, s.rsi, s.adx, s.atr_zone, s.macd_hist);
    else
        snprintf(s.reason, 255,
            "FLAT | TSS=%d | RSI=%.1f | ADX=%.1f | stack=%s | ATR_zone=%s",
            s.tss_score, s.rsi, s.adx,
            bull_stack ? "bull" : (bear_stack ? "bear" : "none"),
            s.atr_zone);

    // Cleanup
    delete[] ema21;  delete[] ema50;  delete[] ema200;
    delete[] rsi_buf; delete[] atr_buf;
    delete[] adx_buf; delete[] dip_buf; delete[] dim_buf;
    delete[] macd_l; delete[] macd_sig; delete[] macd_h;

    return s;
}

} // extern "C"