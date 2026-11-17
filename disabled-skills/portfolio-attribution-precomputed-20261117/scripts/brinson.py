"""Brinson-Fachler allocation and selection including interaction."""
import pandas as pd
from data_store import require


def attribute(wp, wb, industries, returns, benchmark_return):
    stocks = wp.index.union(wb.index)
    missing = stocks.difference(industries.dropna().index)
    require(len(missing)==0, "行业向前填充后仍缺失", stocks=missing.tolist())
    def group(w):
        f = pd.DataFrame({"weight": w, "industry": industries.reindex(w.index), "contribution": w*returns.reindex(w.index)})
        g = f.groupby("industry",observed=True)[["weight", "contribution"]].sum()
        g["return"] = g.contribution/g.weight
        return g
    p, b = group(wp), group(wb)
    rows = []
    for name in p.index.union(b.index):
        pw = float(p.loc[name,"weight"]) if name in p.index else 0.
        bw = float(b.loc[name,"weight"]) if name in b.index else 0.
        br = float(b.loc[name,"return"]) if bw else benchmark_return
        # No portfolio holding: selection/interaction are both defined as zero.
        pr = float(p.loc[name,"return"]) if pw else br
        allocation = (pw-bw)*(br-benchmark_return)
        selection = bw*(pr-br)
        interaction = (pw-bw)*(pr-br)
        rows.append({"industry": name, "portfolio_weight": pw, "benchmark_weight": bw,
                     "active_weight": pw-bw, "portfolio_industry_return": pr, "benchmark_industry_return": br,
                     "benchmark_return_proxy": bw==0, "allocation": allocation, "selection": selection,
                     "interaction": interaction, "selection_including_interaction": pw*(pr-br),
                     "total": allocation+pw*(pr-br)})
    return pd.DataFrame(rows)
