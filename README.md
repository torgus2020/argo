# Argo

Sistema de investigación, backtesting, paper trading y ejecución real para mercado financiero argentino.

El nombre viene del barco de los argonautas: Argentina + algorítmico + búsqueda paciente de un objetivo valioso a través de una travesía larga y disciplinada.

## Filosofía

1. **Modularidad estricta:** estrategias enchufables, datos separados de lógica de ejecución, plataforma agnóstica al broker.
2. **Logueo de TODO**, especialmente lo que sale mal.
3. **Backtest antes de paper, paper antes de real.** Sin atajos.
4. **Promoción y jubilación por criterios cuantitativos**, no por intuición.
5. **Riesgo siempre acotado:** Kelly fraccional, stop-loss, kill switches, drawdown máximo 20%.
6. **Escepticismo activo:** si una idea parece demasiado buena, casi seguro lo es.
7. **Walk-forward de capital:** el sistema escala plata cuando demuestra performance, no antes.

## Estado actual

Ver `roadmap.json` para fase activa, hitos en curso y criterios de cierre. Es la fuente de verdad del proyecto.

## Estructura
argo/
├── config/         # config.json, secrets.json (no commiteado), strategies.json, universe.json
├── src/
│   ├── collectors/ # Conectores a APIs externas (Rava, Polygon, BCRA, INDEC, brokers)
│   ├── strategies/ # Módulos de estrategia (uno por archivo)
│   ├── backtest/   # Engine de backtesting
│   ├── execution/  # Brokers (paper + reales)
│   ├── risk/       # Gestión de riesgo
│   ├── analytics/  # Métricas y attribution
│   ├── roadmap/    # Tracker y alertas de hitos
│   └── utils/      # Utilitarios compartidos (logger, db, etc.)
├── data/           # Datos crudos, procesados, backtests, SQLite (no commiteado)
├── logs/           # Logs rotados diariamente (no commiteados)
├── dashboards/     # HTML local (no commiteado)
├── tests/          # Tests unitarios
├── roadmap.json    # Plan de hitos con criterios cuantitativos
└── roadmap.schema.json  # Schema de validación del roadmap

## Setup inicial

### Requisitos
- Python 3.12 (LTS — decisión tomada en H0.2 tras evaluar fricción con 3.14)
- Git
- Acceso a APIs: Rava Bursátil ($30 USD/mes), Polygon.io (free tier), BCRA, INDEC

### Instalación local (Windows)

```powershell
git clone https://github.com/torgus2020/argo.git C:\Argo
cd C:\Argo
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy config\secrets.json.template config\secrets.json
# Editar config\secrets.json con valores reales
python verificar_instalacion.py
```

### Instalación en VPS (Ubuntu 24.04 LTS)

VPS productivo: DigitalOcean Basic Droplet 2GB / 1 vCPU / 50 GB SSD en NYC3.
Procedimiento documentado en H0.2 del roadmap. Aplicado el 14 de mayo de 2026.

```bash
# Como usuario argo en el VPS
cd ~
git clone git@github.com:torgus2020/argo.git
cd argo
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config/secrets.json.template config/secrets.json
# Editar config/secrets.json con valores reales
python verificar_instalacion.py
```

## Reglas duras del proyecto

1. NUNCA operar real una estrategia sin 90+ días de paper validado.
2. NUNCA superar 20% drawdown global sin kill switch automático.
3. SIEMPRE confirmación manual al inicio (modo `propose_and_confirm` por default).
4. SIEMPRE reconciliación diaria broker vs sistema.
5. SIEMPRE incluir slippage y comisiones realistas en backtest.
6. NUNCA usar leverage al inicio.
7. NUNCA mezclar capital del bot con portfolio personal sin tracking separado.
8. Si el sistema y el operador discrepan, gana el operador.

## Operador

Gus (Buenos Aires). Diseña, valida y decide. No escribe código.

## Licencia

Privado. Sin licencia pública.