"""initial schema

Creates every table for the AsthmaAware data platform, plus the PostGIS
extension the geometry columns depend on.

Generated from db/models/. See db/README.md for what each table is for.

Revision ID: 0001_initial_schema
Revises:
"""

from typing import Sequence, Union

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PostGIS must exist before any geometry column is created. On Supabase
    # and Neon the extension is available but not enabled by default; on a
    # self-hosted Postgres this requires the postgis package to be
    # installed and the connecting role to be superuser.
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    op.create_table('data_sources',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('slug', sa.String(length=128), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('provider', sa.String(length=255), nullable=True),
    sa.Column('url', sa.Text(), nullable=True),
    sa.Column('license', sa.Text(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_data_sources')),
    sa.UniqueConstraint('slug', name=op.f('uq_data_sources_slug'))
    )
    op.create_table('regions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('slug', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('min_lat', sa.Float(), nullable=False),
    sa.Column('max_lat', sa.Float(), nullable=False),
    sa.Column('min_lon', sa.Float(), nullable=False),
    sa.Column('max_lon', sa.Float(), nullable=False),
    sa.Column('grid_rows', sa.Integer(), nullable=False),
    sa.Column('grid_cols', sa.Integer(), nullable=False),
    sa.Column('bbox', geoalchemy2.types.Geometry(geometry_type='POLYGON', srid=4326, dimension=2, spatial_index=False, from_text='ST_GeomFromEWKT', name='geometry'), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('grid_rows > 0 AND grid_cols > 0', name=op.f('ck_regions_grid_positive')),
    sa.CheckConstraint('max_lat > min_lat', name=op.f('ck_regions_lat_ordered')),
    sa.CheckConstraint('max_lon > min_lon', name=op.f('ck_regions_lon_ordered')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_regions')),
    sa.UniqueConstraint('slug', name=op.f('uq_regions_slug'))
    )
    op.create_table('variables',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('slug', sa.String(length=64), nullable=False),
    sa.Column('display_name', sa.String(length=255), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('canonical_unit', sa.String(length=64), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind IN ('satellite', 'ground', 'weather', 'pollen', 'derived')", name=op.f('ck_variables_kind_valid')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_variables')),
    sa.UniqueConstraint('slug', name=op.f('uq_variables_slug'))
    )
    op.create_table('grid_cells',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('region_id', sa.Integer(), nullable=False),
    sa.Column('row', sa.Integer(), nullable=False),
    sa.Column('col', sa.Integer(), nullable=False),
    sa.Column('lat', sa.Float(), nullable=False),
    sa.Column('lon', sa.Float(), nullable=False),
    sa.Column('center_lat', sa.Float(), nullable=False),
    sa.Column('center_lon', sa.Float(), nullable=False),
    sa.Column('centroid', geoalchemy2.types.Geometry(geometry_type='POINT', srid=4326, dimension=2, spatial_index=False, from_text='ST_GeomFromEWKT', name='geometry'), nullable=True),
    sa.Column('geom', geoalchemy2.types.Geometry(geometry_type='POLYGON', srid=4326, dimension=2, spatial_index=False, from_text='ST_GeomFromEWKT', name='geometry'), nullable=True),
    sa.CheckConstraint('row >= 0 AND col >= 0', name=op.f('ck_grid_cells_row_col_nonnegative')),
    sa.ForeignKeyConstraint(['region_id'], ['regions.id'], name=op.f('fk_grid_cells_region_id_regions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_grid_cells')),
    sa.UniqueConstraint('region_id', 'row', 'col', name='uq_grid_cells_region_row_col')
    )
    op.create_index('ix_grid_cells_centroid', 'grid_cells', ['centroid'], unique=False, postgresql_using='gist')
    op.create_index('ix_grid_cells_geom', 'grid_cells', ['geom'], unique=False, postgresql_using='gist')
    op.create_index('ix_grid_cells_region_row_col', 'grid_cells', ['region_id', 'row', 'col'], unique=False)
    op.create_table('ingestion_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('pipeline', sa.String(length=64), nullable=False),
    sa.Column('source_id', sa.Integer(), nullable=True),
    sa.Column('region_id', sa.Integer(), nullable=True),
    sa.Column('requested_start', sa.Date(), nullable=True),
    sa.Column('requested_end', sa.Date(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('rows_written', sa.Integer(), nullable=False),
    sa.Column('params', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.CheckConstraint("status IN ('running', 'success', 'partial', 'failed')", name=op.f('ck_ingestion_runs_status_valid')),
    sa.ForeignKeyConstraint(['region_id'], ['regions.id'], name=op.f('fk_ingestion_runs_region_id_regions'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['source_id'], ['data_sources.id'], name=op.f('fk_ingestion_runs_source_id_data_sources'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ingestion_runs'))
    )
    op.create_index('ix_ingestion_runs_pipeline_started', 'ingestion_runs', ['pipeline', 'started_at'], unique=False)
    op.create_table('model_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('region_id', sa.Integer(), nullable=False),
    sa.Column('model_name', sa.String(length=128), nullable=False),
    sa.Column('model_version', sa.String(length=64), nullable=True),
    sa.Column('model_checksum', sa.String(length=64), nullable=True),
    sa.Column('feature_order', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('scaler_version', sa.String(length=64), nullable=True),
    sa.Column('input_start_date', sa.Date(), nullable=True),
    sa.Column('input_end_date', sa.Date(), nullable=True),
    sa.Column('ran_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['region_id'], ['regions.id'], name=op.f('fk_model_runs_region_id_regions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_model_runs'))
    )
    op.create_index('ix_model_runs_region_ran_at', 'model_runs', ['region_id', 'ran_at'], unique=False)
    op.create_table('scaler_params',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('region_id', sa.Integer(), nullable=False),
    sa.Column('variable_id', sa.Integer(), nullable=False),
    sa.Column('version', sa.String(length=64), nullable=False),
    sa.Column('method', sa.String(length=32), nullable=False),
    sa.Column('fit_min', sa.Float(), nullable=True),
    sa.Column('fit_max', sa.Float(), nullable=True),
    sa.Column('fit_mean', sa.Float(), nullable=True),
    sa.Column('fit_std', sa.Float(), nullable=True),
    sa.Column('fit_start_date', sa.Date(), nullable=True),
    sa.Column('fit_end_date', sa.Date(), nullable=True),
    sa.Column('sample_count', sa.BigInteger(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("method IN ('minmax', 'standard')", name=op.f('ck_scaler_params_method_valid')),
    sa.ForeignKeyConstraint(['region_id'], ['regions.id'], name=op.f('fk_scaler_params_region_id_regions'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['variable_id'], ['variables.id'], name=op.f('fk_scaler_params_variable_id_variables'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_scaler_params')),
    sa.UniqueConstraint('region_id', 'variable_id', 'version', name='uq_scaler_params_region_variable_version')
    )
    op.create_index('ix_scaler_params_active', 'scaler_params', ['region_id', 'variable_id', 'is_active'], unique=False)
    op.create_table('stations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.Column('region_id', sa.Integer(), nullable=True),
    sa.Column('external_id', sa.String(length=128), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=True),
    sa.Column('lat', sa.Float(), nullable=False),
    sa.Column('lon', sa.Float(), nullable=False),
    sa.Column('geom', geoalchemy2.types.Geometry(geometry_type='POINT', srid=4326, dimension=2, spatial_index=False, from_text='ST_GeomFromEWKT', name='geometry'), nullable=True),
    sa.Column('elevation_m', sa.Float(), nullable=True),
    sa.Column('extra', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['region_id'], ['regions.id'], name=op.f('fk_stations_region_id_regions'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['source_id'], ['data_sources.id'], name=op.f('fk_stations_source_id_data_sources'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_stations')),
    sa.UniqueConstraint('source_id', 'external_id', name='uq_stations_source_external_id')
    )
    op.create_index('ix_stations_geom', 'stations', ['geom'], unique=False, postgresql_using='gist')
    op.create_table('zctas',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('zcta', sa.String(length=10), nullable=False),
    sa.Column('region_id', sa.Integer(), nullable=True),
    sa.Column('name', sa.String(length=255), nullable=True),
    sa.Column('area_sq_miles', sa.Float(), nullable=True),
    sa.Column('geom', geoalchemy2.types.Geometry(geometry_type='MULTIPOLYGON', srid=4326, dimension=2, spatial_index=False, from_text='ST_GeomFromEWKT', name='geometry'), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['region_id'], ['regions.id'], name=op.f('fk_zctas_region_id_regions'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_zctas')),
    sa.UniqueConstraint('zcta', name=op.f('uq_zctas_zcta'))
    )
    op.create_index('ix_zctas_geom', 'zctas', ['geom'], unique=False, postgresql_using='gist')
    op.create_table('forecasts',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('model_run_id', sa.Integer(), nullable=False),
    sa.Column('grid_cell_id', sa.Integer(), nullable=False),
    sa.Column('variable_id', sa.Integer(), nullable=False),
    sa.Column('horizon', sa.String(length=16), nullable=False),
    sa.Column('target_date', sa.Date(), nullable=True),
    sa.Column('value', sa.Float(), nullable=True),
    sa.Column('value_scaled', sa.Float(), nullable=True),
    sa.ForeignKeyConstraint(['grid_cell_id'], ['grid_cells.id'], name=op.f('fk_forecasts_grid_cell_id_grid_cells'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['model_run_id'], ['model_runs.id'], name=op.f('fk_forecasts_model_run_id_model_runs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['variable_id'], ['variables.id'], name=op.f('fk_forecasts_variable_id_variables'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_forecasts')),
    sa.UniqueConstraint('model_run_id', 'grid_cell_id', 'variable_id', 'horizon', name='uq_forecasts_run_cell_variable_horizon')
    )
    op.create_index('ix_forecasts_run_horizon', 'forecasts', ['model_run_id', 'horizon'], unique=False)
    op.create_table('ingestion_issues',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('variable_id', sa.Integer(), nullable=True),
    sa.Column('observation_date', sa.Date(), nullable=True),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.CheckConstraint("kind IN ('missing', 'error', 'qa_rejected', 'skipped')", name=op.f('ck_ingestion_issues_kind_valid')),
    sa.ForeignKeyConstraint(['run_id'], ['ingestion_runs.id'], name=op.f('fk_ingestion_issues_run_id_ingestion_runs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['variable_id'], ['variables.id'], name=op.f('fk_ingestion_issues_variable_id_variables'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ingestion_issues'))
    )
    op.create_index('ix_ingestion_issues_variable_date', 'ingestion_issues', ['variable_id', 'observation_date'], unique=False)
    op.create_table('raster_files',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=True),
    sa.Column('region_id', sa.Integer(), nullable=False),
    sa.Column('variable_id', sa.Integer(), nullable=False),
    sa.Column('source_id', sa.Integer(), nullable=True),
    sa.Column('observation_date', sa.Date(), nullable=False),
    sa.Column('source_date', sa.Date(), nullable=True),
    sa.Column('path', sa.Text(), nullable=False),
    sa.Column('unit', sa.String(length=64), nullable=True),
    sa.Column('scale_meters', sa.Float(), nullable=True),
    sa.Column('crs', sa.String(length=32), nullable=True),
    sa.Column('qa_filtering', sa.Text(), nullable=True),
    sa.Column('nodata_value', sa.Float(), nullable=True),
    sa.Column('size_bytes', sa.BigInteger(), nullable=True),
    sa.Column('checksum_sha256', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['region_id'], ['regions.id'], name=op.f('fk_raster_files_region_id_regions'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['run_id'], ['ingestion_runs.id'], name=op.f('fk_raster_files_run_id_ingestion_runs'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['source_id'], ['data_sources.id'], name=op.f('fk_raster_files_source_id_data_sources'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['variable_id'], ['variables.id'], name=op.f('fk_raster_files_variable_id_variables'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_raster_files')),
    sa.UniqueConstraint('region_id', 'variable_id', 'observation_date', name='uq_raster_files_region_variable_date')
    )
    op.create_index('ix_raster_files_variable_date', 'raster_files', ['variable_id', 'observation_date'], unique=False)
    op.create_table('socioeconomic_records',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('zcta_id', sa.Integer(), nullable=False),
    sa.Column('dataset_year', sa.Integer(), nullable=False),
    sa.Column('source', sa.String(length=128), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=True),
    sa.Column('fetched_at', sa.Date(), nullable=True),
    sa.Column('population', sa.Integer(), nullable=True),
    sa.Column('population_density', sa.Float(), nullable=True),
    sa.Column('median_age', sa.Float(), nullable=True),
    sa.Column('children_under_18_rate', sa.Float(), nullable=True),
    sa.Column('seniors_65_plus_rate', sa.Float(), nullable=True),
    sa.Column('median_housing_age', sa.Integer(), nullable=True),
    sa.Column('median_household_income', sa.Integer(), nullable=True),
    sa.Column('median_gross_rent', sa.Integer(), nullable=True),
    sa.Column('median_home_value', sa.Integer(), nullable=True),
    sa.Column('poverty_rate', sa.Float(), nullable=True),
    sa.Column('bachelor_degree_or_higher_rate', sa.Float(), nullable=True),
    sa.Column('no_vehicle_households_rate', sa.Float(), nullable=True),
    sa.Column('severe_rent_burden_rate', sa.Float(), nullable=True),
    sa.Column('overcrowded_housing_rate', sa.Float(), nullable=True),
    sa.Column('unemployment_rate', sa.Float(), nullable=True),
    sa.Column('raw', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['ingestion_runs.id'], name=op.f('fk_socioeconomic_records_run_id_ingestion_runs'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['zcta_id'], ['zctas.id'], name=op.f('fk_socioeconomic_records_zcta_id_zctas'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_socioeconomic_records')),
    sa.UniqueConstraint('zcta_id', 'dataset_year', name='uq_socioeconomic_records_zcta_year')
    )
    op.create_index('ix_socioeconomic_records_year', 'socioeconomic_records', ['dataset_year'], unique=False)
    op.create_table('station_observations',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('station_id', sa.Integer(), nullable=False),
    sa.Column('variable_id', sa.Integer(), nullable=False),
    sa.Column('observed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('value', sa.Float(), nullable=True),
    sa.Column('unit', sa.String(length=64), nullable=True),
    sa.Column('quality_flag', sa.String(length=64), nullable=True),
    sa.Column('run_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['run_id'], ['ingestion_runs.id'], name=op.f('fk_station_observations_run_id_ingestion_runs'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['station_id'], ['stations.id'], name=op.f('fk_station_observations_station_id_stations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['variable_id'], ['variables.id'], name=op.f('fk_station_observations_variable_id_variables'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_station_observations')),
    sa.UniqueConstraint('station_id', 'variable_id', 'observed_at', name='uq_station_observations_station_variable_time')
    )
    op.create_index('ix_station_observations_variable_time', 'station_observations', ['variable_id', 'observed_at'], unique=False)
    op.create_table('grid_observations',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('grid_cell_id', sa.Integer(), nullable=False),
    sa.Column('variable_id', sa.Integer(), nullable=False),
    sa.Column('observation_date', sa.Date(), nullable=False),
    sa.Column('value', sa.Float(), nullable=True),
    sa.Column('source_date', sa.Date(), nullable=True),
    sa.Column('raster_file_id', sa.Integer(), nullable=True),
    sa.Column('run_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['grid_cell_id'], ['grid_cells.id'], name=op.f('fk_grid_observations_grid_cell_id_grid_cells'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['raster_file_id'], ['raster_files.id'], name=op.f('fk_grid_observations_raster_file_id_raster_files'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['run_id'], ['ingestion_runs.id'], name=op.f('fk_grid_observations_run_id_ingestion_runs'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['variable_id'], ['variables.id'], name=op.f('fk_grid_observations_variable_id_variables'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_grid_observations')),
    sa.UniqueConstraint('grid_cell_id', 'variable_id', 'observation_date', name='uq_grid_observations_cell_variable_date')
    )
    op.create_index('ix_grid_observations_cell_variable_date', 'grid_observations', ['grid_cell_id', 'variable_id', 'observation_date'], unique=False)
    op.create_index('ix_grid_observations_variable_date', 'grid_observations', ['variable_id', 'observation_date'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_grid_observations_variable_date', table_name='grid_observations')
    op.drop_index('ix_grid_observations_cell_variable_date', table_name='grid_observations')
    op.drop_table('grid_observations')
    op.drop_index('ix_station_observations_variable_time', table_name='station_observations')
    op.drop_table('station_observations')
    op.drop_index('ix_socioeconomic_records_year', table_name='socioeconomic_records')
    op.drop_table('socioeconomic_records')
    op.drop_index('ix_raster_files_variable_date', table_name='raster_files')
    op.drop_table('raster_files')
    op.drop_index('ix_ingestion_issues_variable_date', table_name='ingestion_issues')
    op.drop_table('ingestion_issues')
    op.drop_index('ix_forecasts_run_horizon', table_name='forecasts')
    op.drop_table('forecasts')
    op.drop_index('ix_zctas_geom', table_name='zctas', postgresql_using='gist')
    op.drop_table('zctas')
    op.drop_index('ix_stations_geom', table_name='stations', postgresql_using='gist')
    op.drop_table('stations')
    op.drop_index('ix_scaler_params_active', table_name='scaler_params')
    op.drop_table('scaler_params')
    op.drop_index('ix_model_runs_region_ran_at', table_name='model_runs')
    op.drop_table('model_runs')
    op.drop_index('ix_ingestion_runs_pipeline_started', table_name='ingestion_runs')
    op.drop_table('ingestion_runs')
    op.drop_index('ix_grid_cells_region_row_col', table_name='grid_cells')
    op.drop_index('ix_grid_cells_geom', table_name='grid_cells', postgresql_using='gist')
    op.drop_index('ix_grid_cells_centroid', table_name='grid_cells', postgresql_using='gist')
    op.drop_table('grid_cells')
    op.drop_table('variables')
    op.drop_table('regions')
    op.drop_table('data_sources')
    # The postgis extension is deliberately not dropped: other schemas in
    # the same database may depend on it.
