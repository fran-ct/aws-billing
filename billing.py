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
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import boto3

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = {
    "ignore_profiles": [],
    "default_months": 6,
    "default_output": "monthly_costs.txt",
    "default_format": "table",
}
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
) -> Dict[str, Decimal]:
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

    totals: Dict[str, Decimal] = {}
    token: Optional[str] = None
    while True:
        if token:
            params["NextPageToken"] = token
        response = ce.get_cost_and_usage(**params)
        for item in response["ResultsByTime"]:
            month = item["TimePeriod"]["Start"]
            amount = Decimal(item["Total"]["UnblendedCost"]["Amount"])
            totals[month] = totals.get(month, Decimal("0")) + amount
        token = response.get("NextPageToken")
        if not token:
            break

    return totals


def main() -> None:
    args = build_parser().parse_args()
    config = load_config()

    default_months = int(config.get("default_months", DEFAULT_CONFIG["default_months"]))
    months = int(args.months) if args.months is not None else default_months
    months = max(1, months)

    if args.exclude_credits and args.only_credits:
        raise SystemExit("No puedes usar --exclude-credits y --only-credits al mismo tiempo.")

    default_output = config.get("default_output", DEFAULT_CONFIG["default_output"])
    reports_dir_config = config.get("reports_dir", DEFAULT_CONFIG.get("reports_dir", "reportes"))
    output_path = Path(args.output) if args.output else Path(reports_dir_config) / default_output

    default_format = str(config.get("default_format", DEFAULT_CONFIG["default_format"])).lower()
    formats_to_generate: List[str]
    if args.format:
        selected = args.format.lower()
        if selected == "all":
            formats_to_generate = ["table", "csv", "tsv"]
        else:
            formats_to_generate = [selected]
    elif args.output:
        suffix = output_path.suffix.lower()
        if suffix == ".csv":
            formats_to_generate = ["csv"]
        elif suffix in {".tsv", ".txt"}:
            formats_to_generate = ["tsv"]
        else:
            formats_to_generate = [default_format]
    else:
        suffix = Path(default_output).suffix.lower()
        if suffix == ".csv":
            formats_to_generate = ["csv"]
        elif suffix in {".tsv", ".txt"}:
            formats_to_generate = ["tsv"]
        else:
            formats_to_generate = [default_format]

    # Si no se especifica formato, generar los tres.
    if not args.format:
        formats_to_generate = ["table", "csv", "tsv"]

    for fmt in list(formats_to_generate):
        if fmt not in {"table", "csv", "tsv"}:
            print(f"# Formato desconocido '{fmt}', se omitirá.")
            formats_to_generate.remove(fmt)

    # Determina rutas finales por formato
    output_files: Dict[str, Path] = {}
    if args.format and args.format.lower() != "all" and args.output:
        base_path = output_path
    else:
        base_path = Path(reports_dir_config) / (args.output.name if args.output else default_output)

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

    for profile in profiles:
        try:
            profile_for_query = None if profile == DEFAULT_PROFILE_LABEL else profile
            totals = query_costs(
                profile_for_query,
                start,
                end,
                args.accounts,
                exclude_credits=exclude_credits,
                only_credits=only_credits,
            )
        except Exception as exc:  # pylint: disable=broad-except
            print(f"# Error consultando perfil {profile}: {exc}")
            continue

        results[DEFAULT_PROFILE_DISPLAY if profile == DEFAULT_PROFILE_LABEL else profile] = totals
        all_months.update(totals.keys())

    if not results:
        raise SystemExit("No se obtuvieron resultados de ningún perfil.")

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

        print(f"# Resultado exportado a {path.resolve()}")


if __name__ == "__main__":
    main()
