#!/usr/bin/env python3
"""
Consulta Cost Explorer y muestra el gasto mensual total.

Características:
- Soporta múltiples perfiles de AWS CLI (SSO o access keys).
- Permite definir valores por defecto mediante `config.json` o la variable
  de entorno `MONTHLY_COSTS_CONFIG`.
- Genera salida en formato tabla (con separadores de miles) y archivo en
  formato tabla o CSV.
"""

import argparse
import csv
import datetime as dt
import json
import os
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union, cast

import boto3

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = {
    "ignore_profiles": [],
    "default_months": 6,
    "output_dir": ".",
    "export_files_by_default": True,
}
DEFAULT_OUTPUT_BASENAME = "monthly_costs"
DEFAULT_PROFILE_LABEL = "__default__"
DEFAULT_PROFILE_DISPLAY = "default"


def load_config() -> Dict[str, Any]:
    config = DEFAULT_CONFIG.copy()
    config_path = os.environ.get("MONTHLY_COSTS_CONFIG")
    path = Path(config_path).expanduser() if config_path else PROJECT_ROOT / "config.json"
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as cfg:
                data = json.load(cfg)
            for key in DEFAULT_CONFIG:
                if key in data:
                    config[key] = data[key]
        except Exception as exc:  # pylint: disable=broad-except
            print(f"# No se pudo leer la configuración en {path}: {exc}")
    return config


def month_start(date: dt.date) -> dt.date:
    return date.replace(day=1)


def shift_months(reference: dt.date, offset: int) -> dt.date:
    """Devuelve el inicio de mes desplazado `offset` meses desde `reference`."""
    month_index = reference.month - 1 + offset
    year = reference.year + month_index // 12
    month = month_index % 12 + 1
    return dt.date(year, month, 1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Obtiene el costo mensual total desde AWS Cost Explorer."
    )
    parser.add_argument(
        "--profile",
        dest="profiles",
        action="append",
        help="Perfil de AWS CLI a usar. Puedes repetir la opción para varios perfiles.",
    )
    parser.add_argument(
        "--months",
        type=int,
        default=None,
        help="Cantidad de meses a incluir (incluye el mes actual).",
    )
    parser.add_argument(
        "--account",
        dest="accounts",
        action="append",
        help="ID de cuenta para filtrar (puedes repetir la opción).",
    )
    parser.add_argument(
        "--all-profiles",
        dest="all_profiles",
        action="store_true",
        help="Ejecutar para todos los perfiles configurados (aplica exclusiones).",
    )
    parser.add_argument(
        "--no-header",
        dest="header",
        action="store_false",
        help="No imprimir la fila de encabezados.",
    )
    parser.add_argument(
        "--exclude-credits",
        dest="exclude_credits",
        action="store_true",
        help="Excluir registros de tipo Credit y Refund al consultar Cost Explorer.",
    )
    parser.add_argument(
        "--only-credits",
        dest="only_credits",
        action="store_true",
        help="Consultar únicamente registros de tipo Credit y Refund.",
    )
    parser.add_argument(
        "--by-account",
        dest="by_account",
        action="store_true",
        help="Desglosar resultados por cuenta vinculada (LINKED_ACCOUNT).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Archivo de salida.",
    )
    parser.add_argument(
        "--format",
        choices=["table", "csv", "tsv", "all"],
        default=None,
        help="Formato de archivo de salida (table, csv, tsv o all).",
    )
    parser.add_argument(
        "--exclude",
        dest="exclude",
        action="append",
        help="Perfiles a excluir cuando se usa --all-profiles (opción repetible). "
        "También puedes usar la variable de entorno MONTHLY_COSTS_EXCLUDE con una lista separada por comas.",
    )
    parser.set_defaults(header=True)
    return parser


def query_costs(
    profile: Optional[str],
    start_date: dt.date,
    end_date: dt.date,
    accounts: Optional[List[str]] = None,
    exclude_credits: bool = False,
    only_credits: bool = False,
    group_by_account: bool = False,
) -> Tuple[Union[Dict[str, Decimal], Dict[str, Dict[str, Decimal]]], Dict[str, str]]:
    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    ce = session.client("ce")

    time_period = {"Start": start_date.isoformat(), "End": end_date.isoformat()}
    params: Dict[str, object] = {
        "TimePeriod": time_period,
        "Granularity": "MONTHLY",
        "Metrics": ["UnblendedCost"],
    }

    filters: List[Dict[str, object]] = []
    if accounts:
        filters.append(
            {
                "Dimensions": {
                    "Key": "LINKED_ACCOUNT",
                    "Values": accounts,
                }
            }
        )
    if only_credits:
        filters.append(
            {
                "Dimensions": {
                    "Key": "RECORD_TYPE",
                    "Values": ["Credit", "Refund"],
                }
            }
        )
    elif exclude_credits:
        filters.append(
            {
                "Not": {
                    "Dimensions": {
                        "Key": "RECORD_TYPE",
                        "Values": ["Credit", "Refund"],
                    }
                }
            }
        )

    if filters:
        params["Filter"] = filters[0] if len(filters) == 1 else {"And": filters}

    if group_by_account:
        params["GroupBy"] = [
            {
                "Type": "DIMENSION",
                "Key": "LINKED_ACCOUNT",
            }
        ]

    if group_by_account:
        totals_by_account: Dict[str, Dict[str, Decimal]] = {}
        account_names: Dict[str, str] = {}
    else:
        totals: Dict[str, Decimal] = {}
        account_names = {}
    token: Optional[str] = None
    while True:
        if token:
            params["NextPageToken"] = token
        response = ce.get_cost_and_usage(**params)
        for item in response["ResultsByTime"]:
            month = item["TimePeriod"]["Start"]
            if group_by_account:
                for group in item.get("Groups", []):
                    account_id = group["Keys"][0]
                    amount = Decimal(group["Metrics"]["UnblendedCost"]["Amount"])
                    account_totals = totals_by_account.setdefault(account_id, {})
                    account_totals[month] = account_totals.get(month, Decimal("0")) + amount
            else:
                amount = Decimal(item["Total"]["UnblendedCost"]["Amount"])
                totals[month] = totals.get(month, Decimal("0")) + amount
        token = response.get("NextPageToken")
        if not token:
            break

    if group_by_account:
        account_names = fetch_account_names(ce, start_date, end_date, list(totals_by_account.keys()))
        return totals_by_account, account_names
    return totals, account_names


def fetch_account_names(
    client,
    start_date: dt.date,
    end_date: dt.date,
    account_ids: Optional[List[str]] = None,
) -> Dict[str, str]:
    params: Dict[str, Any] = {
        "TimePeriod": {"Start": start_date.isoformat(), "End": end_date.isoformat()},
        "Dimension": "LINKED_ACCOUNT",
    }
    if account_ids:
        params["Filter"] = {
            "Dimensions": {
                "Key": "LINKED_ACCOUNT",
                "Values": account_ids,
            }
        }

    names: Dict[str, str] = {}
    token: Optional[str] = None
    while True:
        if token:
            params["NextPageToken"] = token
        response = client.get_dimension_values(**params)
        for item in response.get("DimensionValues", []):
            account_id = item.get("Value")
            if not account_id:
                continue
            attrs = item.get("Attributes", {}) or {}
            description = attrs.get("Description") or attrs.get("description") or ""
            if description:
                names[account_id] = description
        token = response.get("NextPageToken")
        if not token:
            break
    return names


def main() -> None:
    args = build_parser().parse_args()
    config = load_config()

    default_months = int(config.get("default_months", DEFAULT_CONFIG["default_months"]))
    months = int(args.months) if args.months is not None else default_months
    months = max(1, months)

    if args.exclude_credits and args.only_credits:
        raise SystemExit("No puedes usar --exclude-credits y --only-credits al mismo tiempo.")

    output_dir_config = config.get("output_dir", DEFAULT_CONFIG["output_dir"])
    if "output_dir" not in config and "reports_dir" in config:
        output_dir_config = config["reports_dir"]

    output_dir = Path(output_dir_config).expanduser()
    if not output_dir.is_absolute():
        output_dir = (PROJECT_ROOT / output_dir).resolve()

    if args.output:
        output_path = Path(args.output).expanduser()
        if not output_path.is_absolute():
            output_path = (PROJECT_ROOT / output_path).resolve()
    else:
        output_path = output_dir / DEFAULT_OUTPUT_BASENAME

    export_files_by_default = bool(
        config.get("export_files_by_default", DEFAULT_CONFIG["export_files_by_default"])
    )

    formats_to_generate: List[str]
    if args.format:
        selected = args.format.lower()
        if selected == "all":
            formats_to_generate = ["table", "csv", "tsv"]
        else:
            formats_to_generate = [selected]
    elif export_files_by_default:
        formats_to_generate = ["table", "csv", "tsv"]
    else:
        formats_to_generate = []

    for fmt in list(formats_to_generate):
        if fmt not in {"table", "csv", "tsv"}:
            print(f"# Formato desconocido '{fmt}', se omitirá.")
            formats_to_generate.remove(fmt)

    if not formats_to_generate and args.format:
        print("# No se generarán archivos porque no se seleccionaron formatos válidos.")

    # Determina rutas finales por formato
    output_files: Dict[str, Path] = {}
    base_path = output_path

    for fmt in formats_to_generate:
        suffix = base_path.suffix.lower()
        if fmt == "csv":
            target = base_path.with_suffix(".csv") if base_path.suffix else base_path.with_name(f"{base_path.name}.csv")
        elif fmt == "tsv":
            target = base_path.with_suffix(".tsv") if base_path.suffix else base_path.with_name(f"{base_path.name}.tsv")
        else:  # table
            target = base_path.with_suffix(".txt") if suffix in {".csv", ".tsv"} else base_path.with_suffix(".txt") if base_path.suffix else base_path.with_name(f"{base_path.name}.txt")
        if not target.is_absolute():
            target = (PROJECT_ROOT / target).resolve()
        output_files[fmt] = target
        target.parent.mkdir(parents=True, exist_ok=True)

    base_session = boto3.Session()
    available_profiles = base_session.available_profiles
    excluded: Set[str] = set(config.get("ignore_profiles", []))
    env_exclude = os.environ.get("MONTHLY_COSTS_EXCLUDE")
    if env_exclude:
        excluded.update(p.strip() for p in env_exclude.split(",") if p.strip())
    if args.exclude:
        excluded.update(args.exclude)

    if args.profiles:
        profiles = list(dict.fromkeys(args.profiles))
    else:
        profiles = [p for p in available_profiles if p not in excluded]
        if not profiles and base_session.get_credentials() is not None and DEFAULT_PROFILE_DISPLAY not in excluded:
            profiles = [DEFAULT_PROFILE_LABEL]
        if args.all_profiles and not profiles:
            raise SystemExit("No se encontraron perfiles disponibles después de aplicar los excluidos.")
        if not args.all_profiles and not args.profiles:
            if not profiles:
                raise SystemExit("No se encontraron perfiles configurados. Revisa tu archivo ~/.aws/config.")
    if not profiles:
        raise SystemExit("No se encontraron perfiles para consultar.")

    if args.profiles:
        profiles = list(dict.fromkeys(profiles))
    else:
        profiles = sorted(set(profiles))

    today = dt.date.today()
    current_month = month_start(today)
    start = shift_months(current_month, -(months - 1))
    end = shift_months(current_month, 1)

    results: Dict[str, Dict[str, Decimal]] = {}
    all_months: set[str] = set()
    exclude_credits = bool(args.exclude_credits)
    only_credits = bool(args.only_credits)
    by_account = bool(args.by_account)

    status_displayed = False

    def show_status(message: str) -> None:
        nonlocal status_displayed
        if not status_displayed:
            sys.stdout.write("\n")
            status_displayed = True
        else:
            sys.stdout.write("\033[F\033[2K")
        sys.stdout.write(message + "\n")
        sys.stdout.flush()

    def clear_status() -> None:
        nonlocal status_displayed
        if status_displayed:
            sys.stdout.write("\033[F\033[2K")
            sys.stdout.write("\033[F\033[2K")
            sys.stdout.write("\n")
            sys.stdout.flush()
            status_displayed = False

    total_profiles = len(profiles)

    for index, profile in enumerate(profiles, start=1):
        display_name = DEFAULT_PROFILE_DISPLAY if profile == DEFAULT_PROFILE_LABEL else profile
        status_message = f"# Getting data from profile ({index}/{total_profiles}): {display_name}"
        show_status(status_message)

        try:
            profile_for_query = None if profile == DEFAULT_PROFILE_LABEL else profile
            totals, account_names = query_costs(
                profile_for_query,
                start,
                end,
                args.accounts,
                exclude_credits=exclude_credits,
                only_credits=only_credits,
                group_by_account=by_account,
            )
        except Exception as exc:  # pylint: disable=broad-except
            clear_status()
            print(f"# Error consultando perfil {profile}: {exc}")
            continue

        if by_account:
            account_totals = cast(Dict[str, Dict[str, Decimal]], totals)
            if not account_totals:
                continue
            for account_id, month_values in account_totals.items():
                account_label = account_names.get(account_id, account_id)
                row_key = f"{display_name} [{account_label}]"
                results[row_key] = month_values
                all_months.update(month_values.keys())
        else:
            month_totals = cast(Dict[str, Decimal], totals)
            results[display_name] = month_totals
            all_months.update(month_totals.keys())

    if not results:
        raise SystemExit("No se obtuvieron resultados de ningún perfil.")

    clear_status()

    sorted_months = sorted(all_months)
    month_labels = [month[:7] for month in sorted_months]  # YYYY-MM

    # Prepara datos formateados
    table_rows = []
    raw_rows = []
    for profile, totals in results.items():
        row_display = [profile]
        row_raw = [profile]
        for month in sorted_months:
            amount = totals.get(month, Decimal("0")).quantize(Decimal("1"))
            raw_value = str(int(amount))
            display_value = f"{int(amount):,}".replace(",", ".")
            row_display.append(display_value)
            row_raw.append(raw_value)
        table_rows.append(row_display)
        raw_rows.append(row_raw)

    headers = ["profile"] + month_labels
    col_widths_display = [
        max(len(headers[col]), max((len(row[col]) for row in table_rows), default=0))
        for col in range(len(headers))
    ]
    col_widths_file = [
        max(len(headers[col]), max((len(row[col]) for row in raw_rows), default=0))
        for col in range(len(headers))
    ]

    def format_row(values: List[str]) -> str:
        return " | ".join(
            value.ljust(col_widths_display[idx]) if idx == 0 else value.rjust(col_widths_display[idx])
            for idx, value in enumerate(values)
        )

    header_line = format_row(headers)
    separator = "-+-".join("-" * col_widths_display[idx] for idx in range(len(headers)))
    body_lines = [format_row(row) for row in table_rows]

    print()
    if args.header:
        print(header_line)
        print(separator)
    for line in body_lines:
        print(line)
    print()

    # impresión en consola
    def format_row_file(values: List[str]) -> str:
        return " | ".join(
            value.ljust(col_widths_file[idx]) if idx == 0 else value.rjust(col_widths_file[idx])
            for idx, value in enumerate(values)
        )

    export_paths = []
    for fmt, path in output_files.items():
        if fmt == "table":
            header_line_file = format_row_file(headers)
            separator_file = "-+-".join("-" * col_widths_file[idx] for idx in range(len(headers)))
            body_lines_file = [format_row_file(row) for row in raw_rows]

            with path.open("w", encoding="utf-8") as out_file:
                out_file.write(header_line_file + "\n")
                out_file.write(separator_file + "\n")
                for line in body_lines_file:
                    out_file.write(line + "\n")
        elif fmt == "csv":
            with path.open("w", encoding="utf-8", newline="") as out_file:
                writer = csv.writer(out_file)
                writer.writerow(headers)
                for row in raw_rows:
                    writer.writerow(row)
        else:  # tsv
            with path.open("w", encoding="utf-8", newline="") as out_file:
                writer = csv.writer(out_file, delimiter="\t")
                writer.writerow(headers)
                for row in raw_rows:
                    writer.writerow(row)

        export_paths.append(path.resolve())

    if export_paths:
        destinations = sorted({p.parent for p in export_paths})
        if len(destinations) == 1:
            print(f"# Resultados exportados en {destinations[0]}")
        else:
            for dest in destinations:
                print(f"# Resultados exportados en {dest}")


if __name__ == "__main__":
    main()
