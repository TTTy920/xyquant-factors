"""Ten-style cross-sectional WLS, matching the original Barra skill."""
import numpy as np
import pandas as pd
from data_store import FACTORS, require


def regress(x, returns, caps, eligible, minimum=100, max_drop=.1, condition_limit=1e10):
    require(eligible.dropna().isin([0, 1]).all(), "回归资格只能为0/1/空")
    candidates = eligible.index[eligible==1]
    require(len(candidates)>0, "没有合格回归样本")
    xx, yy, cc = x.reindex(candidates), returns.reindex(candidates), caps.reindex(candidates)
    valid = pd.Series(np.isfinite(xx.to_numpy()).all(axis=1),index=xx.index)&np.isfinite(yy)&np.isfinite(cc)&(cc>0)
    n, drop = int(valid.sum()), 1-float(valid.mean())
    require(n>=max(12, minimum), "有效回归样本不足", samples=n, minimum=max(12,minimum))
    require(drop<=max_drop+1e-12, "合格股票缺数据比例超过阈值", dropped_fraction=drop, maximum=max_drop)
    design = np.column_stack([np.ones(n), xx.loc[valid, FACTORS].to_numpy()])
    y = yy[valid].to_numpy()
    scale = cc[valid].to_numpy()**.25
    scale /= np.median(scale)
    try:
        coef, _, rank, singular = np.linalg.lstsq(design*scale[:,None], y*scale, rcond=None)
    except np.linalg.LinAlgError as e:
        from data_store import DataError
        raise DataError("回归求解未收敛") from e
    require(rank==11, "回归矩阵秩不足", rank=int(rank))
    condition = float(singular[0]/singular[-1])
    require(np.isfinite(condition) and condition<=condition_limit and np.isfinite(coef).all(), "回归数值不稳定")
    residual = y-design@coef
    mse = float(np.average(residual**2, weights=scale**2))
    return pd.Series(coef[1:], index=FACTORS), {"sample_count": n, "eligible_count": len(candidates),
        "dropped_fraction": drop, "rank": int(rank), "condition_number": condition,
        "intercept": float(coef[0]), "weighted_residual_rms": float(np.sqrt(mse))}


def attribute(wp, wb, x, returns, caps, eligible, parameters, factor_returns=None):
    stocks = wp.index.union(wb.index)
    held = x.reindex(stocks)
    missing = held.index[~np.isfinite(held.to_numpy()).all(axis=1)]
    require(len(missing)==0, "持仓或基准股票风格暴露缺失", stocks=missing.tolist())
    if factor_returns is None:
        f, diagnostic = regress(x, returns, caps, eligible, **parameters)
    else:
        f = factor_returns.reindex(FACTORS).astype(float)
        require(np.isfinite(f).all(), "现成因子收益率缺失")
        diagnostic = {"source": "supplied_factor_returns"}
    p = wp.to_numpy()@x.reindex(wp.index)[FACTORS].to_numpy()
    b = wb.to_numpy()@x.reindex(wb.index)[FACTORS].to_numpy()
    detail = pd.DataFrame({"factor": FACTORS, "portfolio_exposure": p, "benchmark_exposure": b,
                           "active_exposure": p-b, "factor_return": f.values,
                           "contribution": (p-b)*f.values})
    return detail, diagnostic
