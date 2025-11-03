# AWS Monthly Cost Reporter

Script en Python para consultar AWS Cost Explorer y obtener el costo mensual total por perfil/cuenta. La herramienta genera una tabla legible en consola y un archivo de salida, ideal para comparar meses entre múltiples clientes.

## Requisitos

- Python 3.9 o superior
- AWS CLI v2 configurada (con perfiles SSO o credenciales clásicas)
- Dependencia: [`boto3`](https://boto3.amazonaws.com/)

Instala la dependencia una vez:

```bash
pip install --upgrade boto3
```

## Configura tus perfiles de AWS

Define tus perfiles en `~/.aws/config` y `~/.aws/credentials` según el método de autenticación que utilices. Ejemplo usando IAM Identity Center (SSO):

```
[profile billing-sso]
sso_session    = billing
sso_start_url  = https://<tu-portal>.awsapps.com/start/
sso_region     = us-east-1
sso_account_id = 123456789012
sso_role_name  = FinanceReadOnly
region         = us-east-1

[sso-session billing]
sso_start_url = https://<tu-portal>.awsapps.com/start/
sso_region    = us-east-1
sso_registration_scopes = sso:account:access

[profile cliente-a]
role_arn       = arn:aws:iam::111111111111:role/finance-readonly
source_profile = billing-sso
region         = us-east-1

[profile cliente-b]
role_arn       = arn:aws:iam::222222222222:role/finance-readonly
source_profile = billing-sso
region         = us-east-1
```

Antes de ejecutar el script recuerda renovar la sesión SSO:

```bash
aws sso login --profile billing-sso
```

También puedes usar perfiles basados en access keys; el script funciona con cualquier perfil válido que la AWS CLI reconozca.

## Configuración opcional

Puedes personalizar el comportamiento creando un archivo `config.json` en el directorio raíz (o apuntando a otro con la variable `MONTHLY_COSTS_CONFIG`). Parte del ejemplo incluido:

```bash
cp config.example.json config.json
```

Luego ajusta los campos según tu entorno:

```json
{
  "ignore_profiles": ["billing-sso"],
  "default_months": 6,
  "default_output": "monthly_costs.txt",
  "default_format": "table"
}
```

- `ignore_profiles`: perfiles que se omitirán cuando se consulte a “todos”.
- `default_months`: meses a incluir si no se pasa `--months`.
- `default_output`: nombre base del archivo generado cuando no se indica `--output` (se crea dentro de la carpeta `reportes/` a menos que definas `reports_dir`).
- `default_format`: `table`, `csv`, `tsv` o `all`.
  - Si usas `csv` o `tsv`, la extensión se ajusta automáticamente.
- `reports_dir`: carpeta donde se guardan los reportes cuando no se proporciona ruta explícita (por defecto `reportes/`).

También puedes excluir perfiles temporalmente con la variable `MONTHLY_COSTS_EXCLUDE` (`perfil1,perfil2`).

## Uso del script

```
python3 billing.py [opciones]
```

Opciones principales:

- `--profile PERFIL`: puedes repetirla para incluir uno o varios perfiles específicos. Si no se indica, se usan todos los perfiles disponibles (aplicando exclusiones).
- `--all-profiles`: fuerza la consulta sobre todos los perfiles (comportamiento por defecto) tras aplicar exclusiones.
- `--exclude PERFIL`: excluye un perfil cuando usas `--all-profiles` o no especificas perfiles. Se puede repetir; también puedes definir la variable de entorno `MONTHLY_COSTS_EXCLUDE` con una lista separada por comas.
- `--months N`: incluye el mes actual y los `N-1` anteriores (por defecto 6).
- `--account ID`: filtra por IDs de cuenta específicos (opción repetible).
- `--output RUTA`: archivo de salida; si no se indica, se usa el nombre definido en la configuración dentro de `reportes/`. La extensión se ajusta según el formato.
- `--format`: formato de archivo (`table`, `csv`, `tsv` o `all`). Si no se especifica, se generan automáticamente los tres formatos (`.txt`, `.csv`, `.tsv`), todos en la carpeta `reportes/`.
- `--no-header`: omite la fila de encabezados en la salida estándar.
- `--exclude-credits`: excluye registros de tipo `Credit` y `Refund` (dimensión `RECORD_TYPE`) de la consulta.
- `--only-credits`: limita la consulta a registros de tipo `Credit` y `Refund`. No puede combinarse con `--exclude-credits`.

### Ejemplos

Perfil único usando la variable `AWS_PROFILE`:

```bash
AWS_PROFILE=cliente-a python3 billing.py --months 6 --output reportes/cliente-a.txt
```

Múltiples perfiles explícitos:

```bash
python3 billing.py --profile cliente-a --profile cliente-b --months 3
```

Todos los perfiles configurados (menos los definidos en `MONTHLY_COSTS_EXCLUDE`) y exportar a un archivo legible:

```bash
export MONTHLY_COSTS_EXCLUDE=billing-sso
python3 billing.py --all-profiles --months 12 --output reportes/costos.txt
```

Solo créditos/refunds para un perfil:

```bash
python3 billing.py --profile cliente-a --only-credits --months 3
```

## Formato de salida

- **Consola:** tabla con columnas por mes (ordenadas del más reciente hacia atrás) y montos enteros con separador de miles.
- **Archivo (`--output`):**
  - `table`: misma tabla pero con números enteros sin separador.
  - `csv`: archivo CSV estándar (la consola sigue mostrando la tabla). También se selecciona automáticamente si la ruta termina en `.csv`.
  - `tsv`: archivo con valores separados por tabulaciones (ideal para copiar/pegar).
  - Si no especificas formato, se escriben los tres archivos (`.txt`, `.csv`, `.tsv`) dentro de la carpeta `reportes/`.

## Problemas frecuentes

- `ExpiredToken` o `Could not connect to the endpoint URL`: renueva la sesión SSO (`aws sso login`) o provee access keys vigentes.
- `AccessDenied` al consultar Cost Explorer: el rol/perfil debe tener permisos `ce:GetCostAndUsage`. Agrega también `iam:ListAccountAliases` si necesitas el nombre descriptivo de la cuenta.

## Licencia

Uso libre para internalizar reportes de costos. Adecúalo a tus políticas y añade autenticación/seguridad según sea necesario.
