#include <cmath>
#include <algorithm>
#include <cstring>
using namespace std;

extern "C" {

// ── EMA ───────────────────────────────────────────────────────────────────
void calc_ema(const double* close, int n, int period, double* out) {
    if (n <= 0 || period <= 0) return;
    double k = 2.0 / (period + 1.0);
    out[0] = close[0];
    for (int i = 1; i < n; i++)
        out[i] = close[i] * k + out[i - 1] * (1.0 - k);
}

// ── RSI ───────────────────────────────────────────────────────────────────
void calc_rsi(const double* close, int n, int period, double* out) {
    if (n < period + 1) return;
    double avg_gain = 0.0, avg_loss = 0.0;
    for (int i = 1; i <= period; i++) {
        double diff = close[i] - close[i - 1];
        if (diff > 0) avg_gain += diff;
        else          avg_loss -= diff;
    }
    avg_gain /= period;
    avg_loss /= period;
    out[period] = (avg_loss == 0.0) ? 100.0
                : 100.0 - (100.0 / (1.0 + avg_gain / avg_loss));
    for (int i = period + 1; i < n; i++) {
        double diff = close[i] - close[i - 1];
        double gain = diff > 0 ? diff : 0.0;
        double loss = diff < 0 ? -diff : 0.0;
        avg_gain = (avg_gain * (period - 1) + gain) / period;
        avg_loss = (avg_loss * (period - 1) + loss) / period;
        out[i] = (avg_loss == 0.0) ? 100.0
               : 100.0 - (100.0 / (1.0 + avg_gain / avg_loss));
    }
}

// ── ATR ───────────────────────────────────────────────────────────────────
void calc_atr(const double* high, const double* low,
              const double* close, int n, int period, double* out) {
    if (n < 2) return;
    double sum = 0.0;
    for (int i = 1; i < n; i++) {
        double tr = max({ high[i] - low[i],
                          fabs(high[i] - close[i - 1]),
                          fabs(low[i]  - close[i - 1]) });
        if (i < period) {
            sum += tr;
            out[i] = sum / i;
        } else if (i == period) {
            sum += tr;
            out[i] = sum / period;
        } else {
            out[i] = (out[i - 1] * (period - 1) + tr) / period;
        }
    }
}

// ── ADX / DI ──────────────────────────────────────────────────────────────
void calc_adx(const double* high, const double* low,
              const double* close, int n, int period,
              double* adx_out, double* di_plus_out, double* di_minus_out) {
    if (n < period * 2) return;
    double* tr_buf    = new double[n]();
    double* dm_plus   = new double[n]();
    double* dm_minus  = new double[n]();
    double* atr_s     = new double[n]();
    double* dp_s      = new double[n]();
    double* dm_s      = new double[n]();
    double* dx_buf    = new double[n]();

    for (int i = 1; i < n; i++) {
        double up   = high[i]  - high[i - 1];
        double down = low[i - 1] - low[i];
        tr_buf[i] = max({ high[i] - low[i],
                          fabs(high[i] - close[i - 1]),
                          fabs(low[i]  - close[i - 1]) });
        dm_plus[i]  = (up > down && up > 0) ? up : 0;
        dm_minus[i] = (down > up && down > 0) ? down : 0;
    }

    double tr_sum = 0, dp_sum = 0, dm_sum = 0;
    for (int i = 1; i <= period; i++) {
        tr_sum += tr_buf[i];
        dp_sum += dm_plus[i];
        dm_sum += dm_minus[i];
    }
    atr_s[period] = tr_sum;
    dp_s[period]  = dp_sum;
    dm_s[period]  = dm_sum;

    for (int i = period + 1; i < n; i++) {
        atr_s[i] = atr_s[i-1] - atr_s[i-1]/period + tr_buf[i];
        dp_s[i]  = dp_s[i-1]  - dp_s[i-1]/period  + dm_plus[i];
        dm_s[i]  = dm_s[i-1]  - dm_s[i-1]/period  + dm_minus[i];
    }

    double dx_sum = 0;
    for (int i = period; i < n; i++) {
        double dip  = (atr_s[i] == 0) ? 0 : 100.0 * dp_s[i] / atr_s[i];
        double dim  = (atr_s[i] == 0) ? 0 : 100.0 * dm_s[i] / atr_s[i];
        di_plus_out[i]  = dip;
        di_minus_out[i] = dim;
        double dsum = dip + dim;
        dx_buf[i] = (dsum == 0) ? 0 : 100.0 * fabs(dip - dim) / dsum;
        if (i < period * 2) {
            dx_sum += dx_buf[i];
        } else if (i == period * 2) {
            dx_sum += dx_buf[i];
            adx_out[i] = dx_sum / period;
        } else {
            adx_out[i] = (adx_out[i-1] * (period-1) + dx_buf[i]) / period;
        }
    }

    delete[] tr_buf; delete[] dm_plus; delete[] dm_minus;
    delete[] atr_s;  delete[] dp_s;   delete[] dm_s; delete[] dx_buf;
}

// ── MACD ──────────────────────────────────────────────────────────────────
void calc_macd(const double* close, int n,
               int fast, int slow, int signal_p,
               double* macd_line, double* signal_line, double* histogram) {
    double* ema_fast = new double[n]();
    double* ema_slow = new double[n]();
    calc_ema(close, n, fast, ema_fast);
    calc_ema(close, n, slow, ema_slow);
    for (int i = 0; i < n; i++)
        macd_line[i] = ema_fast[i] - ema_slow[i];
    calc_ema(macd_line, n, signal_p, signal_line);
    for (int i = 0; i < n; i++)
        histogram[i] = macd_line[i] - signal_line[i];
    delete[] ema_fast;
    delete[] ema_slow;
}

// ── ATR rolling average (for zone detection) ─────────────────────────────
double atr_rolling_avg(const double* atr_vals, int n, int avg_period) {
    if (n < avg_period) return 0.0;
    double sum = 0.0;
    for (int i = n - avg_period; i < n; i++)
        sum += atr_vals[i];
    return sum / avg_period;
}

} // extern "C"