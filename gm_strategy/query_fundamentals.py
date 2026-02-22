# -*- coding: utf-8 -*-
"""
掘金：单只股票详细基本面查询脚本

使用前需设置 token：环境变量 GM_TOKEN，或修改下方 GM_TOKEN 默认值。
依赖：pip install gm pandas

用法：
  python gm_strategy/query_fundamentals.py SHSE.600000
  python gm_strategy/query_fundamentals.py 600000
  python gm_strategy/query_fundamentals.py SZSE.000001 --date 2024-06-01
  # 扩展字段：以 + 开头表示在默认字段后追加
  python gm_strategy/query_fundamentals.py 600089 --balance-fields "+goodwill,oth_ast"
  # 多报告期 + 同比增长率（按报告日区间）
  python gm_strategy/query_fundamentals.py 600089 --start-date 2022-01-01 --end-date 2024-12-31
  # 多报告期且请求衍生指标（字段名以掘金文档为准）
  python gm_strategy/query_fundamentals.py 600089 --start-date 2022-01-01 --end-date 2024-12-31 --deriv-fields roe,roa
  # 多报告期不计算同比
  python gm_strategy/query_fundamentals.py 600089 --start-date 2022-01-01 --end-date 2024-12-31 --no-yoy
"""

import argparse
import os
import sys

# 掘金 SDK（依赖 C 扩展 gm.csdk.c_sdk，仅 pip install gm 可能不包含，需在掘金终端或完整 SDK 环境运行）
try:
    from gm.api import (
        set_token,
        stk_get_fundamentals_balance_pt,
        stk_get_fundamentals_cashflow_pt,
        stk_get_fundamentals_income_pt,
        stk_get_finance_prime_pt,
        stk_get_fundamentals_balance,
        stk_get_fundamentals_cashflow,
        stk_get_fundamentals_income,
        stk_get_finance_prime,
        stk_get_finance_deriv,
        stk_get_finance_deriv_pt,
    )
except ImportError as e:
    err = str(e).strip()
    if "c_sdk" in err or "gm.csdk" in err:
        print("掘金 SDK 的 C 扩展未就绪（缺少 gm.csdk.c_sdk）。", file=sys.stderr)
        print("请在「掘金终端」内运行本脚本，或从掘金官网下载并安装完整 Python SDK。", file=sys.stderr)
    else:
        print("请先安装掘金 SDK: pip install gm", file=sys.stderr)
    print(f"当前 Python: {sys.executable}", file=sys.stderr)
    print(f"若使用 .venv，请先激活再安装: .venv\\Scripts\\Activate.ps1 然后 pip install gm", file=sys.stderr)
    sys.exit(1)

GM_TOKEN = os.environ.get("GM_TOKEN", "your_token_here")

# 默认请求字段（可被传入参数覆盖或扩展，掘金单次最多约 20 个字段）
DEFAULT_BALANCE_FIELDS = (
    "ttl_ast,ttl_liab,ttl_eqy,mny_cptl,acct_rcv,invt,fix_ast,intg_ast,gw,"
    "paid_in_cptl,cptl_rsv,ret_prof,ttl_eqy_pcom,sht_ln,acct_pay,bnd_pay,lt_ln"
)
DEFAULT_INCOME_FIELDS = (
    "ttl_inc_oper,inc_oper,cost_oper,exp_sell,exp_adm,exp_rd,exp_fin,"
    "oper_prof,ttl_prof,net_prof,net_prof_pcom,eps_base,eps_dil"
)
DEFAULT_CASHFLOW_FIELDS = (
    "net_cf_oper,net_cf_inv,net_cf_fin,net_incr_cash_eq,cash_cash_eq_end,"
    "cash_rcv_sale,cash_pur_gds_svc,cash_pay_emp,cash_pay_tax"
)
DEFAULT_PRIME_FIELDS = (
    "roe,roe_weight_avg,eps_basic,eps_dil,bps_pcom_ps,net_cf_oper_ps,"
    "ttl_ast,ttl_liab,share_cptl,inc_oper,oper_prof,net_prof_pcom,ttl_eqy_pcom"
)
# 财务衍生指标：仅当传入 --deriv-fields 时请求（掘金字段名以文档为准，此处不设默认避免报错）
DEFAULT_DERIV_FIELDS = ""


def _add_yoy_growth(df, numeric_columns=None, rpt_date_col="rpt_date", rpt_type_col="rpt_type"):
    """
    在按报告期排序的 DataFrame 上，按「上年同期」（同一 rpt_type，报告日差一年）计算同比增长率。
    新增列名为 原列名_yoy，值为 (本期 - 上年同期) / 上年同期；无上年同期则为 None。
    """
    if df is None or df.empty:
        return df
    import pandas as pd
    out = df.copy()
    out[rpt_date_col] = pd.to_datetime(out[rpt_date_col], errors="coerce")
    out = out.sort_values([rpt_type_col, rpt_date_col]).reset_index(drop=True)
    if numeric_columns is None:
        numeric_columns = [
            c for c in out.columns
            if c not in (rpt_date_col, rpt_type_col, "symbol", "pub_date", "data_type")
            and pd.api.types.is_numeric_dtype(out[c])
        ]
    # 上年同期：rpt_date - 1 年，同一 rpt_type
    out["_prev_year"] = out[rpt_date_col] - pd.DateOffset(years=1)
    for col in numeric_columns:
        if col not in out.columns:
            continue
        yoy_col = f"{col}_yoy"
        out[yoy_col] = None
        for rpt_type in out[rpt_type_col].dropna().unique():
            block = out[out[rpt_type_col] == rpt_type].copy()
            block = block.sort_values(rpt_date_col)
            for i in range(len(block)):
                idx = block.index[i]
                prev_date = out.loc[idx, "_prev_year"]
                if pd.isna(prev_date):
                    continue
                try:
                    prev_d = prev_date.date()
                except (AttributeError, TypeError, ValueError):
                    continue
                prev_row = block[block[rpt_date_col].dt.date == prev_d]
                if prev_row.empty:
                    continue
                prev_val = prev_row[col].iloc[0]
                cur_val = out.loc[idx, col]
                try:
                    pv, cv = float(prev_val), float(cur_val)
                    if pv != 0 and pv == pv:
                        out.loc[idx, yoy_col] = (cv - pv) / pv
                except (TypeError, ValueError):
                    pass
    out.drop(columns=["_prev_year"], inplace=True, errors="ignore")
    return out


def _resolve_fields(default: str, override: str | None) -> str:
    """若 override 为 None 用 default；若以 '+' 开头则拼在 default 后；否则用 override 整体替换。"""
    if not override or not override.strip():
        return default
    s = override.strip()
    if s.startswith("+"):
        extra = s[1:].strip().strip(",")
        return f"{default},{extra}" if extra else default
    return s


def _normalize_symbol(s: str) -> str:
    """600000 -> SHSE.600000, 000001 -> SZSE.000001"""
    s = s.strip().upper()
    if s.startswith("SHSE.") or s.startswith("SZSE."):
        return s
    if s.startswith("6"):
        return f"SHSE.{s}"
    return f"SZSE.{s}"


def _fmt_num(x, unit="元") -> str:
    if x is None or (isinstance(x, float) and (x != x or x == 0)):
        return "-"
    try:
        x = float(x)
    except (TypeError, ValueError):
        return str(x)
    if abs(x) >= 1e8:
        return f"{x/1e8:.2f}亿{unit}"
    if abs(x) >= 1e4:
        return f"{x/1e4:.2f}万{unit}"
    return f"{x:.2f}{unit}"


def query_fundamentals(
    symbol: str,
    date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    balance_fields: str | None = None,
    income_fields: str | None = None,
    cashflow_fields: str | None = None,
    prime_fields: str | None = None,
    deriv_fields: str | None = None,
    add_yoy: bool = True,
) -> dict:
    """
    查询单只股票基本面。
    - 若仅传 date（或都不传）：截面查询，返回当日可用的最新报告期（_pt 接口）。
    - 若传 start_date 且 end_date：多报告期查询，返回区间内所有报告期（非 _pt 接口），
      并对主要指标计算同比增长率（原列名_yoy），除非 add_yoy=False。

    symbol: 掘金格式 SHSE.600000 或 6 位代码
    date: 截面查询日期 YYYY-MM-DD
    start_date / end_date: 多报告期起止（报告日期），如 "2022-01-01", "2024-12-31"
    *_fields: 各表字段，逗号分隔；以 '+' 开头表示在默认后追加
    add_yoy: 多报告期时是否计算同比
    返回 dict：symbol, date/start_date/end_date, balance, income, cashflow, prime, deriv（可选）
    """
    symbol = _normalize_symbol(symbol)
    multi = start_date and end_date

    if multi:
        bf = _resolve_fields(DEFAULT_BALANCE_FIELDS, balance_fields)
        inf = _resolve_fields(DEFAULT_INCOME_FIELDS, income_fields)
        cf = _resolve_fields(DEFAULT_CASHFLOW_FIELDS, cashflow_fields)
        pf = _resolve_fields(DEFAULT_PRIME_FIELDS, prime_fields)
        balance = income = cashflow = prime = deriv = None
        try:
            balance = stk_get_fundamentals_balance(
                symbol=symbol, fields=bf, start_date=start_date, end_date=end_date, df=True
            )
        except Exception as e:
            print(f"资产负债表(多期)查询失败: {e}", file=sys.stderr)
        try:
            income = stk_get_fundamentals_income(
                symbol=symbol, fields=inf, start_date=start_date, end_date=end_date, df=True
            )
        except Exception as e:
            print(f"利润表(多期)查询失败: {e}", file=sys.stderr)
        try:
            cashflow = stk_get_fundamentals_cashflow(
                symbol=symbol, fields=cf, start_date=start_date, end_date=end_date, df=True
            )
        except Exception as e:
            print(f"现金流量表(多期)查询失败: {e}", file=sys.stderr)
        try:
            prime = stk_get_finance_prime(
                symbol=symbol, fields=pf, start_date=start_date, end_date=end_date, df=True
            )
            if prime is not None and not prime.empty and add_yoy:
                prime = _add_yoy_growth(prime)
        except Exception as e:
            print(f"财务主要指标(多期)查询失败: {e}", file=sys.stderr)
        df_str = _resolve_fields(DEFAULT_DERIV_FIELDS, deriv_fields) if deriv_fields else ""
        if df_str and df_str.strip():
            try:
                deriv = stk_get_finance_deriv(
                    symbol=symbol, fields=df_str, start_date=start_date, end_date=end_date, df=True
                )
            except Exception as e:
                deriv = None
                print(f"财务衍生指标(多期)查询失败(可忽略): {e}", file=sys.stderr)
        else:
            deriv = None
        return {
            "symbol": symbol,
            "start_date": start_date,
            "end_date": end_date,
            "balance": balance,
            "income": income,
            "cashflow": cashflow,
            "prime": prime,
            "deriv": deriv,
        }

    # 截面
    bf = _resolve_fields(DEFAULT_BALANCE_FIELDS, balance_fields)
    try:
        balance = stk_get_fundamentals_balance_pt(
            symbols=symbol, date=date, fields=bf, df=True
        )
    except Exception as e:
        balance = None
        print(f"资产负债表查询失败: {e}", file=sys.stderr)

    inf = _resolve_fields(DEFAULT_INCOME_FIELDS, income_fields)
    try:
        income = stk_get_fundamentals_income_pt(
            symbols=symbol, date=date, fields=inf, df=True
        )
    except Exception as e:
        income = None
        print(f"利润表查询失败: {e}", file=sys.stderr)

    cf = _resolve_fields(DEFAULT_CASHFLOW_FIELDS, cashflow_fields)
    try:
        cashflow = stk_get_fundamentals_cashflow_pt(
            symbols=symbol, date=date, fields=cf, df=True
        )
    except Exception as e:
        cashflow = None
        print(f"现金流量表查询失败: {e}", file=sys.stderr)

    pf = _resolve_fields(DEFAULT_PRIME_FIELDS, prime_fields)
    try:
        prime = stk_get_finance_prime_pt(
            symbols=symbol, date=date, fields=pf, df=True
        )
    except Exception as e:
        prime = None
        print(f"财务主要指标查询失败: {e}", file=sys.stderr)

    deriv = None
    df_str = _resolve_fields(DEFAULT_DERIV_FIELDS, deriv_fields) if deriv_fields else ""
    if df_str and df_str.strip():
        try:
            deriv = stk_get_finance_deriv_pt(
                symbols=symbol, date=date, fields=df_str, df=True
            )
        except Exception as e:
            print(f"财务衍生指标查询失败(可忽略): {e}", file=sys.stderr)

    return {
        "symbol": symbol,
        "date": date,
        "balance": balance,
        "income": income,
        "cashflow": cashflow,
        "prime": prime,
        "deriv": deriv,
    }


def _fmt_pct(x) -> str:
    if x is None or (isinstance(x, float) and (x != x or x == 0 and x != 0)):
        return "-"
    try:
        return f"{float(x) * 100:.2f}%"
    except (TypeError, ValueError):
        return str(x)


def print_report(data: dict) -> None:
    """将 query_fundamentals 的返回结果打印为可读文本（截面或多报告期）。"""
    symbol = data["symbol"]
    multi = "start_date" in data and data.get("start_date")
    if multi:
        title = f"标的: {symbol}  多报告期: {data['start_date']} ~ {data['end_date']}"
    else:
        title = f"标的: {symbol}  查询日期(发布日期): {data.get('date') or '最新'}"
    print("=" * 60)
    print(title)
    print("=" * 60)

    prime = data.get("prime")
    if prime is not None and not prime.empty:
        if len(prime) == 1:
            print("\n【财务主要指标】")
            row = prime.iloc[0]
            rpt = row.get("rpt_date") or row.get("pub_date")
            print(f"  报告期: {rpt}")
            for k in ["roe", "roe_weight_avg", "eps_basic", "eps_dil", "bps_pcom_ps", "net_cf_oper_ps"]:
                if k in row:
                    v = row[k]
                    if k.startswith("roe"):
                        print(f"  {k}: {v}" if v == v else f"  {k}: -")
                    else:
                        print(f"  {k}: {_fmt_num(v)}")
            for k in ["ttl_ast", "ttl_liab", "inc_oper", "net_prof_pcom", "ttl_eqy_pcom"]:
                if k in row:
                    print(f"  {k}: {_fmt_num(row[k])}")
        else:
            print("\n【财务主要指标 - 多报告期】")
            cols = ["rpt_date", "rpt_type", "inc_oper", "net_prof_pcom", "roe"]
            yoy_cols = [c for c in prime.columns if c.endswith("_yoy")]
            show = [c for c in cols if c in prime.columns] + yoy_cols[:5]
            if show:
                print(prime[show].to_string(index=False))
            if yoy_cols:
                print("  (列名_yoy 为同比增长率)")

    income = data.get("income")
    if income is not None and not income.empty:
        if len(income) == 1:
            print("\n【利润表摘要】")
            row = income.iloc[0]
            for k in ["inc_oper", "cost_oper", "oper_prof", "net_prof_pcom", "eps_base", "eps_dil"]:
                if k in row:
                    print(f"  {k}: {_fmt_num(row[k])}")
        else:
            print("\n【利润表 - 多报告期】")
            cols = [c for c in ["rpt_date", "rpt_type", "inc_oper", "net_prof_pcom"] if c in income.columns]
            print(income[cols].head(12).to_string(index=False))

    balance = data.get("balance")
    if balance is not None and not balance.empty:
        if len(balance) == 1:
            print("\n【资产负债表摘要】")
            row = balance.iloc[0]
            for k in ["ttl_ast", "ttl_liab", "ttl_eqy", "mny_cptl", "paid_in_cptl", "ret_prof"]:
                if k in row:
                    print(f"  {k}: {_fmt_num(row[k])}")
        else:
            print("\n【资产负债表 - 多报告期】")
            cols = [c for c in ["rpt_date", "rpt_type", "ttl_ast", "ttl_liab", "ttl_eqy"] if c in balance.columns]
            print(balance[cols].head(12).to_string(index=False))

    cashflow = data.get("cashflow")
    if cashflow is not None and not cashflow.empty:
        if len(cashflow) == 1:
            print("\n【现金流量摘要】")
            row = cashflow.iloc[0]
            for k in ["net_cf_oper", "net_cf_inv", "net_cf_fin", "net_incr_cash_eq", "cash_cash_eq_end"]:
                if k in row:
                    print(f"  {k}: {_fmt_num(row[k])}")
        else:
            print("\n【现金流量 - 多报告期】")
            cols = [c for c in ["rpt_date", "rpt_type", "net_cf_oper", "net_incr_cash_eq"] if c in cashflow.columns]
            print(cashflow[cols].head(12).to_string(index=False))

    deriv = data.get("deriv")
    if deriv is not None and not deriv.empty:
        print("\n【财务衍生指标】")
        if len(deriv) == 1:
            row = deriv.iloc[0]
            for k in deriv.columns:
                if k in ("symbol", "pub_date", "rpt_date", "rpt_type", "data_type"):
                    continue
                v = row.get(k)
                if v is not None and (not isinstance(v, float) or v == v):
                    print(f"  {k}: {v}")
        else:
            cols = [c for c in ["rpt_date", "rpt_type"] if c in deriv.columns] + [c for c in deriv.columns if c not in ("symbol", "pub_date", "rpt_date", "rpt_type", "data_type")][:8]
            print(deriv[cols].head(12).to_string(index=False))

    print()


def main():
    parser = argparse.ArgumentParser(description="掘金：单只股票基本面查询")
    parser.add_argument("symbol", help="股票代码，如 SHSE.600000 或 600000")
    parser.add_argument("--date", "-d", default=None, help="查询日期 YYYY-MM-DD，默认最新")
    parser.add_argument("--token", "-t", default=GM_TOKEN, help="掘金 token，默认用 GM_TOKEN 或脚本内默认值")
    parser.add_argument(
        "--balance-fields",
        default=None,
        help="资产负债表字段，逗号分隔。以 + 开头表示在默认字段后追加，如 '+goodwill,oth_ast'",
    )
    parser.add_argument(
        "--income-fields",
        default=None,
        help="利润表字段，逗号分隔。以 + 开头表示在默认字段后追加",
    )
    parser.add_argument(
        "--cashflow-fields",
        default=None,
        help="现金流量表字段，逗号分隔。以 + 开头表示在默认字段后追加",
    )
    parser.add_argument(
        "--prime-fields",
        default=None,
        help="财务主要指标字段，逗号分隔。以 + 开头表示在默认字段后追加",
    )
    parser.add_argument(
        "--start-date",
        default=None,
        help="多报告期起始日期(报告日) YYYY-MM-DD，需与 --end-date 同时使用",
    )
    parser.add_argument(
        "--end-date",
        default=None,
        help="多报告期结束日期(报告日) YYYY-MM-DD，需与 --start-date 同时使用",
    )
    parser.add_argument(
        "--deriv-fields",
        default=None,
        help="财务衍生指标字段，逗号分隔。仅当传入时请求衍生指标(多期或截面)",
    )
    parser.add_argument(
        "--no-yoy",
        action="store_true",
        help="多报告期时不计算同比增长率",
    )
    args = parser.parse_args()

    if not args.token or args.token == "your_token_here":
        print("请设置环境变量 GM_TOKEN 或使用 --token 传入掘金 token", file=sys.stderr)
        sys.exit(1)
    if (args.start_date and not args.end_date) or (args.end_date and not args.start_date):
        print("多报告期需同时指定 --start-date 与 --end-date", file=sys.stderr)
        sys.exit(1)

    set_token(args.token)
    data = query_fundamentals(
        args.symbol,
        date=args.date,
        start_date=args.start_date,
        end_date=args.end_date,
        balance_fields=args.balance_fields,
        income_fields=args.income_fields,
        cashflow_fields=args.cashflow_fields,
        prime_fields=args.prime_fields,
        deriv_fields=args.deriv_fields,
        add_yoy=not args.no_yoy,
    )
    print_report(data)


if __name__ == "__main__":
    main()
