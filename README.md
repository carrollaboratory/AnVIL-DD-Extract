# AnVIL-DD-Extract

AnVIL-DD-Extract is a Python-based tool designed to extract and process study metadata and data dictionaries from the AnVIL platform and dbGaP. It allows users to search for studies by title, retrieve relevant metadata, and convert XML-based data dictionaries into CSV format for easier analysis. Additionally, it supports creating data dictionaries from the Terra Data Repository (TDR), which is part of the AnVIL platform.

## Features

- Search studies in the DUOS index by title.
- Extract detailed metadata for matching studies.
- Retrieve and process data dictionaries from dbGaP.
- Create data dictionaries from the TDR.
- Save results in structured CSV files for further analysis.

## Installation

1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd AnVIL-DD-Extract
   ```

2. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Usage

### Refresh the DUOS Dataset Index

Authenticate with Google Application Default Credentials in the environment
that has access to DUOS, then fetch the current dataset index:

```bash
gcloud auth application-default login
python refresh_duos_datasets.py
```

If Application Default Credentials are not available, provide an existing
access token through the environment instead:

```bash
export DUOS_ACCESS_TOKEN="<access-token>"
python refresh_duos_datasets.py
```

The complete, unmodified dataset records returned by the paginated DUOS search
are stored as `data/dataset_results/datasets_YYYYMMDD.json`. A successful
refresh on the same date replaces that date's file.

### Search for Studies

Run the `ADDE.py` script to search the newest downloaded dataset index by title
and extract metadata:

```bash
python ADDE.py --query "<search-string>"
```

- **Arguments**:
  - `--query`: The search string to match study titles.
  - `--dataset-file`: Optional path to a specific versioned dataset JSON file.

- **Output**:
  - Results are saved in the `data/query_results/<sanitized_study_name>` directory as `study_results.csv`.



## Files

- `ADDE.py`: Main script for searching studies and extracting metadata.
- `refresh_duos_datasets.py`: Retrieves and versions the current DUOS dataset index.
- `phs2dd.py`: Script for retrieving and converting data dictionaries from dbGaP.
- `tdr2dd.py`: Script for creating data dictionaries from the TDR.
- `requirements.txt`: List of Python dependencies.
- `data/dataset_results`: Directory where versioned DUOS responses are saved.
- `data/query_results`: Directory where query results are saved.

## Example Workflow

1. Search for studies with a specific title:
   ```bash
   python ADDE.py --query "Cancer"
   ```

## Dependencies

- Python 3.7+
- pandas
- requests
- BeautifulSoup4
- lxml

Install dependencies using:
```bash
pip install -r requirements.txt
```

## Logging

Logs for data dictionary processing are saved in the specified output directory as `phs2dd.log`

## License

This project is licensed under the MIT License. See the LICENSE file for details.

