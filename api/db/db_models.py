#
#  Copyright 2024 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
import hashlib
import inspect
import logging
import operator
import os
import sys
import time
import typing
from datetime import datetime, timezone
from enum import Enum
from functools import wraps

from quart_auth import AuthUser
from itsdangerous.url_safe import URLSafeTimedSerializer as Serializer
from peewee import (
    fn,
    InterfaceError,
    OperationalError,
    ProgrammingError,
    BigIntegerField,
    BooleanField,
    CharField,
    CompositeKey,
    DateTimeField,
    Field,
    FloatField,
    IntegerField,
    Metadata,
    Model,
    TextField,
    PrimaryKeyField,
)
from playhouse.migrate import MySQLMigrator, PostgresqlMigrator, migrate
from playhouse.pool import PooledMySQLDatabase, PooledPostgresqlDatabase

from api import utils
from api.db import SerializedType
from api.utils.json_encode import json_dumps, json_loads
from api.utils.configs import deserialize_b64, serialize_b64

from common.time_utils import current_timestamp, timestamp_to_date, date_string_to_timestamp
from common.decorator import singleton
from common.constants import ParserType
from common import settings


CONTINUOUS_FIELD_TYPE = {IntegerField, FloatField, DateTimeField}
AUTO_DATE_TIMESTAMP_FIELD_PREFIX = {"create", "start", "end", "update", "read_access", "write_access"}


class TextFieldType(Enum):
    MYSQL = "LONGTEXT"
    OCEANBASE = "LONGTEXT"
    POSTGRES = "TEXT"


class LongTextField(TextField):
    field_type = TextFieldType[settings.DATABASE_TYPE.upper()].value


class JSONField(LongTextField):
    default_value = {}

    def __init__(self, object_hook=None, object_pairs_hook=None, **kwargs):
        self._object_hook = object_hook
        self._object_pairs_hook = object_pairs_hook
        super().__init__(**kwargs)

    def db_value(self, value):
        if value is None:
            value = self.default_value
        return json_dumps(value)

    def python_value(self, value):
        if not value:
            return self.default_value
        return json_loads(value, object_hook=self._object_hook, object_pairs_hook=self._object_pairs_hook)


class ListField(JSONField):
    default_value = []


class SerializedField(LongTextField):
    def __init__(self, serialized_type=SerializedType.PICKLE, object_hook=None, object_pairs_hook=None, **kwargs):
        self._serialized_type = serialized_type
        self._object_hook = object_hook
        self._object_pairs_hook = object_pairs_hook
        super().__init__(**kwargs)

    def db_value(self, value):
        if self._serialized_type == SerializedType.PICKLE:
            return serialize_b64(value, to_str=True)
        elif self._serialized_type == SerializedType.JSON:
            if value is None:
                return None
            return json_dumps(value, with_type=True)
        else:
            raise ValueError(f"the serialized type {self._serialized_type} is not supported")

    def python_value(self, value):
        if self._serialized_type == SerializedType.PICKLE:
            return deserialize_b64(value)
        elif self._serialized_type == SerializedType.JSON:
            if value is None:
                return {}
            return json_loads(value, object_hook=self._object_hook, object_pairs_hook=self._object_pairs_hook)
        else:
            raise ValueError(f"the serialized type {self._serialized_type} is not supported")


def is_continuous_field(cls: typing.Type) -> bool:
    if cls in CONTINUOUS_FIELD_TYPE:
        return True
    for p in cls.__bases__:
        if p in CONTINUOUS_FIELD_TYPE:
            return True
        elif p is not Field and p is not object:
            if is_continuous_field(p):
                return True
    else:
        return False


def auto_date_timestamp_field():
    return {f"{f}_time" for f in AUTO_DATE_TIMESTAMP_FIELD_PREFIX}


def auto_date_timestamp_db_field():
    return {f"f_{f}_time" for f in AUTO_DATE_TIMESTAMP_FIELD_PREFIX}


def remove_field_name_prefix(field_name):
    return field_name[2:] if field_name.startswith("f_") else field_name


class BaseModel(Model):
    create_time = BigIntegerField(null=True, index=True)
    create_date = DateTimeField(null=True, index=True)
    update_time = BigIntegerField(null=True, index=True)
    update_date = DateTimeField(null=True, index=True)

    def to_json(self):
        # This function is obsolete
        return self.to_dict()

    def to_dict(self):
        return self.__dict__["__data__"]

    def to_human_model_dict(self, only_primary_with: list = None):
        model_dict = self.__dict__["__data__"]

        if not only_primary_with:
            return {remove_field_name_prefix(k): v for k, v in model_dict.items()}

        human_model_dict = {}
        for k in self._meta.primary_key.field_names:
            human_model_dict[remove_field_name_prefix(k)] = model_dict[k]
        for k in only_primary_with:
            human_model_dict[k] = model_dict[f"f_{k}"]
        return human_model_dict

    @property
    def meta(self) -> Metadata:
        return self._meta

    @classmethod
    def get_primary_keys_name(cls):
        return cls._meta.primary_key.field_names if isinstance(cls._meta.primary_key, CompositeKey) else [cls._meta.primary_key.name]

    @classmethod
    def getter_by(cls, attr):
        return operator.attrgetter(attr)(cls)

    @classmethod
    def query(cls, reverse=None, order_by=None, **kwargs):
        filters = []
        for f_n, f_v in kwargs.items():
            attr_name = "%s" % f_n
            if not hasattr(cls, attr_name) or f_v is None:
                continue
            if type(f_v) in {list, set}:
                f_v = list(f_v)
                if is_continuous_field(type(getattr(cls, attr_name))):
                    if len(f_v) == 2:
                        for i, v in enumerate(f_v):
                            if isinstance(v, str) and f_n in auto_date_timestamp_field():
                                # time type: %Y-%m-%d %H:%M:%S
                                f_v[i] = date_string_to_timestamp(v)
                        lt_value = f_v[0]
                        gt_value = f_v[1]
                        if lt_value is not None and gt_value is not None:
                            filters.append(cls.getter_by(attr_name).between(lt_value, gt_value))
                        elif lt_value is not None:
                            filters.append(operator.attrgetter(attr_name)(cls) >= lt_value)
                        elif gt_value is not None:
                            filters.append(operator.attrgetter(attr_name)(cls) <= gt_value)
                else:
                    filters.append(operator.attrgetter(attr_name)(cls) << f_v)
            else:
                filters.append(operator.attrgetter(attr_name)(cls) == f_v)
        if filters:
            query_records = cls.select().where(*filters)
            if reverse is not None:
                if not order_by or not hasattr(cls, f"{order_by}"):
                    order_by = "create_time"
                if reverse is True:
                    query_records = query_records.order_by(cls.getter_by(f"{order_by}").desc())
                elif reverse is False:
                    query_records = query_records.order_by(cls.getter_by(f"{order_by}").asc())
            return [query_record for query_record in query_records]
        else:
            return []

    @classmethod
    def insert(cls, __data=None, **insert):
        if isinstance(__data, dict) and __data:
            __data[cls._meta.combined["create_time"]] = current_timestamp()
        if insert:
            insert["create_time"] = current_timestamp()

        return super().insert(__data, **insert)

    # update and insert will call this method
    @classmethod
    def _normalize_data(cls, data, kwargs):
        normalized = super()._normalize_data(data, kwargs)
        if not normalized:
            return {}

        normalized[cls._meta.combined["update_time"]] = current_timestamp()

        for f_n in AUTO_DATE_TIMESTAMP_FIELD_PREFIX:
            if {f"{f_n}_time", f"{f_n}_date"}.issubset(cls._meta.combined.keys()) and cls._meta.combined[f"{f_n}_time"] in normalized and normalized[cls._meta.combined[f"{f_n}_time"]] is not None:
                normalized[cls._meta.combined[f"{f_n}_date"]] = timestamp_to_date(normalized[cls._meta.combined[f"{f_n}_time"]])

        return normalized


class JsonSerializedField(SerializedField):
    def __init__(self, object_hook=utils.from_dict_hook, object_pairs_hook=None, **kwargs):
        super(JsonSerializedField, self).__init__(serialized_type=SerializedType.JSON, object_hook=object_hook, object_pairs_hook=object_pairs_hook, **kwargs)


class RetryingPooledMySQLDatabase(PooledMySQLDatabase):
    def __init__(self, *args, **kwargs):
        self.max_retries = kwargs.pop("max_retries", 5)
        self.retry_delay = kwargs.pop("retry_delay", 1)
        super().__init__(*args, **kwargs)

    def execute_sql(self, sql, params=None, commit=True):
        for attempt in range(self.max_retries + 1):
            try:
                return super().execute_sql(sql, params, commit)
            except (OperationalError, InterfaceError) as e:
                error_codes = [2013, 2006]
                error_messages = ['', 'Lost connection']
                should_retry = (
                    (hasattr(e, 'args') and e.args and e.args[0] in error_codes) or
                    (str(e) in error_messages) or
                    (hasattr(e, '__class__') and e.__class__.__name__ == 'InterfaceError')
                )

                if should_retry and attempt < self.max_retries:
                    logging.warning(
                        f"Database connection issue (attempt {attempt+1}/{self.max_retries}): {e}"
                    )
                    self._handle_connection_loss()
                    time.sleep(self.retry_delay * (2 ** attempt))
                else:
                    logging.error(f"DB execution failure: {e}")
                    raise
        return None

    def _handle_connection_loss(self):
        # self.close_all()
        # self.connect()
        try:
            self.close()
        except Exception:
            pass
        try:
            self.connect()
        except Exception as e:
            logging.error(f"Failed to reconnect: {e}")
            time.sleep(0.1)
            try:
                self.connect()
            except Exception as e2:
                logging.error(f"Failed to reconnect on second attempt: {e2}")
                raise

    def begin(self):
        for attempt in range(self.max_retries + 1):
            try:
                return super().begin()
            except (OperationalError, InterfaceError) as e:
                error_codes = [2013, 2006]
                error_messages = ['', 'Lost connection']

                should_retry = (
                    (hasattr(e, 'args') and e.args and e.args[0] in error_codes) or
                    (str(e) in error_messages) or
                    (hasattr(e, '__class__') and e.__class__.__name__ == 'InterfaceError')
                )

                if should_retry and attempt < self.max_retries:
                    logging.warning(
                        f"Lost connection during transaction (attempt {attempt+1}/{self.max_retries})"
                    )
                    self._handle_connection_loss()
                    time.sleep(self.retry_delay * (2 ** attempt))
                else:
                    raise
        return None


class RetryingPooledPostgresqlDatabase(PooledPostgresqlDatabase):
    def __init__(self, *args, **kwargs):
        self.max_retries = kwargs.pop("max_retries", 5)
        self.retry_delay = kwargs.pop("retry_delay", 1)
        super().__init__(*args, **kwargs)

    def execute_sql(self, sql, params=None, commit=True):
        for attempt in range(self.max_retries + 1):
            try:
                return super().execute_sql(sql, params, commit)
            except (OperationalError, InterfaceError) as e:
                # PostgreSQL specific error codes
                # 57P01: admin_shutdown
                # 57P02: crash_shutdown
                # 57P03: cannot_connect_now
                # 08006: connection_failure
                # 08003: connection_does_not_exist
                # 08000: connection_exception
                error_messages = ['connection', 'server closed', 'connection refused',
                                'no connection to the server', 'terminating connection']

                should_retry = any(msg in str(e).lower() for msg in error_messages)

                if should_retry and attempt < self.max_retries:
                    logging.warning(
                        f"PostgreSQL connection issue (attempt {attempt+1}/{self.max_retries}): {e}"
                    )
                    self._handle_connection_loss()
                    time.sleep(self.retry_delay * (2 ** attempt))
                else:
                    logging.error(f"PostgreSQL execution failure: {e}")
                    raise
        return None

    def _handle_connection_loss(self):
        try:
            self.close()
        except Exception:
            pass
        try:
            self.connect()
        except Exception as e:
            logging.error(f"Failed to reconnect to PostgreSQL: {e}")
            time.sleep(0.1)
            try:
                self.connect()
            except Exception as e2:
                logging.error(f"Failed to reconnect to PostgreSQL on second attempt: {e2}")
                raise

    def begin(self):
        for attempt in range(self.max_retries + 1):
            try:
                return super().begin()
            except (OperationalError, InterfaceError) as e:
                error_messages = ['connection', 'server closed', 'connection refused',
                                'no connection to the server', 'terminating connection']

                should_retry = any(msg in str(e).lower() for msg in error_messages)

                if should_retry and attempt < self.max_retries:
                    logging.warning(
                        f"PostgreSQL connection lost during transaction (attempt {attempt+1}/{self.max_retries})"
                    )
                    self._handle_connection_loss()
                    time.sleep(self.retry_delay * (2 ** attempt))
                else:
                    raise
        return None


class RetryingPooledOceanBaseDatabase(PooledMySQLDatabase):
    """Pooled OceanBase database with retry mechanism.

    OceanBase is compatible with MySQL protocol, so we inherit from PooledMySQLDatabase.
    This class provides connection pooling and automatic retry for connection issues.
    """
    def __init__(self, *args, **kwargs):
        self.max_retries = kwargs.pop("max_retries", 5)
        self.retry_delay = kwargs.pop("retry_delay", 1)
        super().__init__(*args, **kwargs)

    def execute_sql(self, sql, params=None, commit=True):
        for attempt in range(self.max_retries + 1):
            try:
                return super().execute_sql(sql, params, commit)
            except (OperationalError, InterfaceError) as e:
                # OceanBase/MySQL specific error codes
                # 2013: Lost connection to MySQL server during query
                # 2006: MySQL server has gone away
                error_codes = [2013, 2006]
                error_messages = ['', 'Lost connection', 'gone away']

                should_retry = (
                    (hasattr(e, 'args') and e.args and e.args[0] in error_codes) or
                    any(msg in str(e).lower() for msg in error_messages) or
                    (hasattr(e, '__class__') and e.__class__.__name__ == 'InterfaceError')
                )

                if should_retry and attempt < self.max_retries:
                    logging.warning(
                        f"OceanBase connection issue (attempt {attempt+1}/{self.max_retries}): {e}"
                    )
                    self._handle_connection_loss()
                    time.sleep(self.retry_delay * (2 ** attempt))
                else:
                    logging.error(f"OceanBase execution failure: {e}")
                    raise
        return None

    def _handle_connection_loss(self):
        try:
            self.close()
        except Exception:
            pass
        try:
            self.connect()
        except Exception as e:
            logging.error(f"Failed to reconnect to OceanBase: {e}")
            time.sleep(0.1)
            try:
                self.connect()
            except Exception as e2:
                logging.error(f"Failed to reconnect to OceanBase on second attempt: {e2}")
                raise

    def begin(self):
        for attempt in range(self.max_retries + 1):
            try:
                return super().begin()
            except (OperationalError, InterfaceError) as e:
                error_codes = [2013, 2006]
                error_messages = ['', 'Lost connection']

                should_retry = (
                    (hasattr(e, 'args') and e.args and e.args[0] in error_codes) or
                    (str(e) in error_messages) or
                    (hasattr(e, '__class__') and e.__class__.__name__ == 'InterfaceError')
                )

                if should_retry and attempt < self.max_retries:
                    logging.warning(
                        f"Lost connection during transaction (attempt {attempt+1}/{self.max_retries})"
                    )
                    self._handle_connection_loss()
                    time.sleep(self.retry_delay * (2 ** attempt))
                else:
                    raise
        return None


class PooledDatabase(Enum):
    MYSQL = RetryingPooledMySQLDatabase
    OCEANBASE = RetryingPooledOceanBaseDatabase
    POSTGRES = RetryingPooledPostgresqlDatabase


class DatabaseMigrator(Enum):
    MYSQL = MySQLMigrator
    OCEANBASE = MySQLMigrator
    POSTGRES = PostgresqlMigrator


@singleton
class BaseDataBase:
    def __init__(self):
        database_config = settings.DATABASE.copy()
        db_name = database_config.pop("name")

        pool_config = {
            'max_retries': 5,
            'retry_delay': 1,
        }
        database_config.update(pool_config)
        self.database_connection = PooledDatabase[settings.DATABASE_TYPE.upper()].value(
            db_name, **database_config
        )
        # self.database_connection = PooledDatabase[settings.DATABASE_TYPE.upper()].value(db_name, **database_config)
        logging.info("init database on cluster mode successfully")


def with_retry(max_retries=3, retry_delay=1.0):
    """Decorator: Add retry mechanism to database operations

    Args:
        max_retries (int): maximum number of retries
        retry_delay (float): initial retry delay (seconds), will increase exponentially

    Returns:
        decorated function
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for retry in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    # get self and method name for logging
                    self_obj = args[0] if args else None
                    func_name = func.__name__
                    lock_name = getattr(self_obj, "lock_name", "unknown") if self_obj else "unknown"

                    if retry < max_retries - 1:
                        current_delay = retry_delay * (2**retry)
                        logging.warning(f"{func_name} {lock_name} failed: {str(e)}, retrying ({retry + 1}/{max_retries})")
                        time.sleep(current_delay)
                    else:
                        logging.error(f"{func_name} {lock_name} failed after all attempts: {str(e)}")

            if last_exception:
                raise last_exception
            return False

        return wrapper

    return decorator


class PostgresDatabaseLock:
    def __init__(self, lock_name, timeout=10, db=None):
        self.lock_name = lock_name
        self.lock_id = int(hashlib.md5(lock_name.encode()).hexdigest(), 16) % (2**31 - 1)
        self.timeout = int(timeout)
        self.db = db if db else DB

    @with_retry(max_retries=3, retry_delay=1.0)
    def lock(self):
        cursor = self.db.execute_sql("SELECT pg_try_advisory_lock(%s)", (self.lock_id,))
        ret = cursor.fetchone()
        if ret[0] == 0:
            raise Exception(f"acquire postgres lock {self.lock_name} timeout")
        elif ret[0] == 1:
            return True
        else:
            raise Exception(f"failed to acquire lock {self.lock_name}")

    @with_retry(max_retries=3, retry_delay=1.0)
    def unlock(self):
        cursor = self.db.execute_sql("SELECT pg_advisory_unlock(%s)", (self.lock_id,))
        ret = cursor.fetchone()
        if ret[0] == 0:
            raise Exception(f"postgres lock {self.lock_name} was not established by this thread")
        elif ret[0] == 1:
            return True
        else:
            raise Exception(f"postgres lock {self.lock_name} does not exist")

    def __enter__(self):
        if isinstance(self.db, PooledPostgresqlDatabase):
            self.lock()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if isinstance(self.db, PooledPostgresqlDatabase):
            self.unlock()

    def __call__(self, func):
        @wraps(func)
        def magic(*args, **kwargs):
            with self:
                return func(*args, **kwargs)

        return magic


class MysqlDatabaseLock:
    def __init__(self, lock_name, timeout=10, db=None):
        self.lock_name = lock_name
        self.timeout = int(timeout)
        self.db = db if db else DB

    @with_retry(max_retries=3, retry_delay=1.0)
    def lock(self):
        # SQL parameters only support %s format placeholders
        cursor = self.db.execute_sql("SELECT GET_LOCK(%s, %s)", (self.lock_name, self.timeout))
        ret = cursor.fetchone()
        if ret[0] == 0:
            raise Exception(f"acquire mysql lock {self.lock_name} timeout")
        elif ret[0] == 1:
            return True
        else:
            raise Exception(f"failed to acquire lock {self.lock_name}")

    @with_retry(max_retries=3, retry_delay=1.0)
    def unlock(self):
        cursor = self.db.execute_sql("SELECT RELEASE_LOCK(%s)", (self.lock_name,))
        ret = cursor.fetchone()
        if ret[0] == 0:
            raise Exception(f"mysql lock {self.lock_name} was not established by this thread")
        elif ret[0] == 1:
            return True
        else:
            raise Exception(f"mysql lock {self.lock_name} does not exist")

    def __enter__(self):
        if isinstance(self.db, PooledMySQLDatabase):
            self.lock()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if isinstance(self.db, PooledMySQLDatabase):
            self.unlock()

    def __call__(self, func):
        @wraps(func)
        def magic(*args, **kwargs):
            with self:
                return func(*args, **kwargs)

        return magic


class DatabaseLock(Enum):
    MYSQL = MysqlDatabaseLock
    OCEANBASE = MysqlDatabaseLock
    POSTGRES = PostgresDatabaseLock


DB = BaseDataBase().database_connection
DB.lock = DatabaseLock[settings.DATABASE_TYPE.upper()].value


def close_connection():
    try:
        if DB:
            DB.close_stale(age=30)
    except Exception as e:
        logging.exception(e)


class DataBaseModel(BaseModel):
    class Meta:
        database = DB


@DB.connection_context()
@DB.lock("init_database_tables", 60)
def init_database_tables(alter_fields=[]):
    members = inspect.getmembers(sys.modules[__name__], inspect.isclass)
    table_objs = []
    create_failed_list = []
    for name, obj in members:
        if obj != DataBaseModel and issubclass(obj, DataBaseModel):
            table_objs.append(obj)

            if not obj.table_exists():
                logging.debug(f"start create table {obj.__name__}")
                try:
                    obj.create_table(safe=True)
                    logging.debug(f"create table success: {obj.__name__}")
                except Exception as e:
                    logging.exception(e)
                    create_failed_list.append(obj.__name__)
            else:
                logging.debug(f"table {obj.__name__} already exists, skip creation.")

    if create_failed_list:
        logging.error(f"create tables failed: {create_failed_list}")
        raise Exception(f"create tables failed: {create_failed_list}")
    migrate_db()


def fill_db_model_object(model_object, human_model_dict):
    for k, v in human_model_dict.items():
        attr_name = "%s" % k
        if hasattr(model_object.__class__, attr_name):
            setattr(model_object, attr_name, v)
    return model_object


class User(DataBaseModel, AuthUser):
    id = CharField(max_length=32, primary_key=True)
    access_token = CharField(max_length=255, null=True, index=True)
    nickname = CharField(max_length=100, null=False, help_text="nicky name", index=True)
    password = CharField(max_length=255, null=True, help_text="password", index=True)
    email = CharField(max_length=255, null=False, help_text="email", unique=True)
    avatar = TextField(null=True, help_text="avatar base64 string")
    language = CharField(max_length=32, null=True, help_text="English|Chinese", default="Chinese" if "zh_CN" in os.getenv("LANG", "") else "English", index=True)
    color_schema = CharField(max_length=32, null=True, help_text="Bright|Dark", default="Bright", index=True)
    timezone = CharField(max_length=64, null=True, help_text="Timezone", default="UTC+8\tAsia/Shanghai", index=True)
    last_login_time = DateTimeField(null=True, index=True)
    is_authenticated = CharField(max_length=1, null=False, default="1", index=True)
    is_active = CharField(max_length=1, null=False, default="1", index=True)
    is_anonymous = CharField(max_length=1, null=False, default="0", index=True)
    login_channel = CharField(null=True, help_text="from which user login", index=True)
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)
    is_superuser = BooleanField(null=True, help_text="is root", default=False, index=True)

    def __str__(self):
        return self.email

    def get_id(self):
        jwt = Serializer(secret_key=settings.SECRET_KEY)
        return jwt.dumps(str(self.access_token))

    class Meta:
        db_table = "user"


class Tenant(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    name = CharField(max_length=100, null=True, help_text="Tenant name", index=True)
    public_key = CharField(max_length=255, null=True, index=True)
    llm_id = CharField(max_length=128, null=False, help_text="default llm ID", index=True)
    tenant_llm_id = IntegerField(null=True, help_text="id in tenant_llm", index=True)
    embd_id = CharField(max_length=128, null=False, help_text="default embedding model ID", index=True)
    tenant_embd_id = IntegerField(null=True, help_text="id in tenant_llm", index=True)
    asr_id = CharField(max_length=128, null=False, help_text="default ASR model ID", index=True)
    tenant_asr_id = IntegerField(null=True, help_text="id in tenant_llm", index=True)
    img2txt_id = CharField(max_length=128, null=False, help_text="default image to text model ID", index=True)
    tenant_img2txt_id = IntegerField(null=True, help_text="id in tenant_llm", index=True)
    rerank_id = CharField(max_length=128, null=False, help_text="default rerank model ID", index=True)
    tenant_rerank_id = IntegerField(null=True, help_text="id in tenant_llm", index=True)
    tts_id = CharField(max_length=256, null=True, help_text="default tts model ID", index=True)
    tenant_tts_id = IntegerField(null=True, help_text="id in tenant_llm", index=True)
    parser_ids = CharField(max_length=256, null=False, help_text="document processors", index=True)
    credit = IntegerField(default=512, index=True)
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    class Meta:
        db_table = "tenant"


class UserTenant(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    user_id = CharField(max_length=32, null=False, index=True)
    tenant_id = CharField(max_length=32, null=False, index=True)
    role = CharField(max_length=32, null=False, help_text="UserTenantRole", index=True)
    invited_by = CharField(max_length=32, null=False, index=True)
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    class Meta:
        db_table = "user_tenant"


class InvitationCode(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    code = CharField(max_length=32, null=False, index=True)
    visit_time = DateTimeField(null=True, index=True)
    user_id = CharField(max_length=32, null=True, index=True)
    tenant_id = CharField(max_length=32, null=True, index=True)
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    class Meta:
        db_table = "invitation_code"


class LLMFactories(DataBaseModel):
    name = CharField(max_length=128, null=False, help_text="LLM factory name", primary_key=True)
    logo = TextField(null=True, help_text="llm logo base64")
    tags = CharField(max_length=255, null=False, help_text="LLM, Text Embedding, Image2Text, ASR", index=True)
    rank = IntegerField(default=0, index=False)
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    def __str__(self):
        return self.name

    class Meta:
        db_table = "llm_factories"


class LLM(DataBaseModel):
    # LLMs dictionary
    llm_name = CharField(max_length=128, null=False, help_text="LLM name", index=True)
    model_type = CharField(max_length=128, null=False, help_text="LLM, Text Embedding, Image2Text, ASR", index=True)
    fid = CharField(max_length=128, null=False, help_text="LLM factory id", index=True)
    max_tokens = IntegerField(default=0)

    tags = CharField(max_length=255, null=False, help_text="LLM, Text Embedding, Image2Text, Chat, 32k...", index=True)
    is_tools = BooleanField(null=False, help_text="support tools", default=False)
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    def __str__(self):
        return self.llm_name

    class Meta:
        primary_key = CompositeKey("fid", "llm_name")
        db_table = "llm"


class TenantLLM(DataBaseModel):
    id = PrimaryKeyField()
    tenant_id = CharField(max_length=32, null=False, index=True)
    llm_factory = CharField(max_length=128, null=False, help_text="LLM factory name", index=True)
    model_type = CharField(max_length=128, null=True, help_text="LLM, Text Embedding, Image2Text, ASR", index=True)
    llm_name = CharField(max_length=128, null=True, help_text="LLM name", default="", index=True)
    api_key = TextField(null=True, help_text="API KEY")
    api_base = CharField(max_length=255, null=True, help_text="API Base")
    max_tokens = IntegerField(default=8192, help_text="Max context token num", index=True)
    used_tokens = IntegerField(default=0, help_text="Used token num", index=True)
    status = CharField(max_length=1, null=False, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    def __str__(self):
        return self.llm_name

    class Meta:
        db_table = "tenant_llm"
        indexes = (
            (("tenant_id", "llm_factory", "llm_name"), True),
        )


class TenantLangfuse(DataBaseModel):
    tenant_id = CharField(max_length=32, null=False, primary_key=True)
    secret_key = CharField(max_length=2048, null=False, help_text="SECRET KEY", index=True)
    public_key = CharField(max_length=2048, null=False, help_text="PUBLIC KEY", index=True)
    host = CharField(max_length=128, null=False, help_text="HOST", index=True)

    def __str__(self):
        return "Langfuse host" + self.host

    class Meta:
        db_table = "tenant_langfuse"


class Knowledgebase(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    avatar = TextField(null=True, help_text="avatar base64 string")
    tenant_id = CharField(max_length=32, null=False, index=True)
    name = CharField(max_length=128, null=False, help_text="KB name", index=True)
    language = CharField(max_length=32, null=True, default="Chinese" if "zh_CN" in os.getenv("LANG", "") else "English", help_text="English|Chinese", index=True)
    description = TextField(null=True, help_text="KB description")
    embd_id = CharField(max_length=128, null=False, help_text="default embedding model ID", index=True)
    tenant_embd_id = IntegerField(null=True, help_text="id in tenant_llm", index=True)
    permission = CharField(max_length=16, null=False, help_text="me|team", default="me", index=True)
    created_by = CharField(max_length=32, null=False, index=True)
    doc_num = IntegerField(default=0, index=True)
    token_num = IntegerField(default=0, index=True)
    chunk_num = IntegerField(default=0, index=True)
    similarity_threshold = FloatField(default=0.2, index=True)
    vector_similarity_weight = FloatField(default=0.3, index=True)

    parser_id = CharField(max_length=32, null=False, help_text="default parser ID", default=ParserType.NAIVE.value, index=True)
    pipeline_id = CharField(max_length=32, null=True, help_text="Pipeline ID", index=True)
    parser_config = JSONField(null=False, default={"pages": [[1, 1000000]], "table_context_size": 0, "image_context_size": 0})
    pagerank = IntegerField(default=0, index=False)

    graphrag_task_id = CharField(max_length=32, null=True, help_text="Graph RAG task ID", index=True)
    graphrag_task_finish_at = DateTimeField(null=True)
    raptor_task_id = CharField(max_length=32, null=True, help_text="RAPTOR task ID", index=True)
    raptor_task_finish_at = DateTimeField(null=True)
    mindmap_task_id = CharField(max_length=32, null=True, help_text="Mindmap task ID", index=True)
    mindmap_task_finish_at = DateTimeField(null=True)

    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    def __str__(self):
        return self.name

    class Meta:
        db_table = "knowledgebase"


class Document(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    thumbnail = TextField(null=True, help_text="thumbnail base64 string")
    kb_id = CharField(max_length=256, null=False, index=True)
    parser_id = CharField(max_length=32, null=False, help_text="default parser ID", index=True)
    pipeline_id = CharField(max_length=32, null=True, help_text="pipeline ID", index=True)
    parser_config = JSONField(null=False, default={"pages": [[1, 1000000]], "table_context_size": 0, "image_context_size": 0})
    source_type = CharField(max_length=128, null=False, default="local", help_text="where dose this document come from", index=True)
    type = CharField(max_length=32, null=False, help_text="file extension", index=True)
    created_by = CharField(max_length=32, null=False, help_text="who created it", index=True)
    name = CharField(max_length=255, null=True, help_text="file name", index=True)
    location = CharField(max_length=255, null=True, help_text="where dose it store", index=True)
    size = BigIntegerField(default=0, index=True)
    token_num = IntegerField(default=0, index=True)
    chunk_num = IntegerField(default=0, index=True)
    progress = FloatField(default=0, index=True)
    progress_msg = TextField(null=True, help_text="process message", default="")
    process_begin_at = DateTimeField(null=True, index=True)
    process_duration = FloatField(default=0)
    suffix = CharField(max_length=32, null=False, help_text="The real file extension suffix", index=True)

    content_hash = CharField(max_length=32, null=True, help_text="xxhash128 of document content for change detection", default="", index=True)

    run = CharField(max_length=1, null=True, help_text="start to run processing or cancel.(1: run it; 2: cancel)", default="0", index=True)
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    class Meta:
        db_table = "document"


class File(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    parent_id = CharField(max_length=32, null=False, help_text="parent folder id", index=True)
    tenant_id = CharField(max_length=32, null=False, help_text="tenant id", index=True)
    created_by = CharField(max_length=32, null=False, help_text="who created it", index=True)
    name = CharField(max_length=255, null=False, help_text="file name or folder name", index=True)
    location = CharField(max_length=255, null=True, help_text="where dose it store", index=True)
    size = BigIntegerField(default=0, index=True)
    type = CharField(max_length=32, null=False, help_text="file extension", index=True)
    source_type = CharField(max_length=128, null=False, default="", help_text="where dose this document come from", index=True)

    class Meta:
        db_table = "file"


class File2Document(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    file_id = CharField(max_length=32, null=True, help_text="file id", index=True)
    document_id = CharField(max_length=32, null=True, help_text="document id", index=True)

    class Meta:
        db_table = "file2document"


class Task(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    doc_id = CharField(max_length=32, null=False, index=True)
    from_page = IntegerField(default=0)
    to_page = IntegerField(default=100000000)
    task_type = CharField(max_length=32, null=False, default="")
    priority = IntegerField(default=0)

    begin_at = DateTimeField(null=True, index=True)
    process_duration = FloatField(default=0)

    progress = FloatField(default=0, index=True)
    progress_msg = TextField(null=True, help_text="process message", default="")
    retry_count = IntegerField(default=0)
    digest = TextField(null=True, help_text="task digest", default="")
    chunk_ids = LongTextField(null=True, help_text="chunk ids", default="")


class Dialog(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    tenant_id = CharField(max_length=32, null=False, index=True)
    name = CharField(max_length=255, null=True, help_text="dialog application name", index=True)
    description = TextField(null=True, help_text="Dialog description")
    icon = TextField(null=True, help_text="icon base64 string")
    language = CharField(max_length=32, null=True, default="Chinese" if "zh_CN" in os.getenv("LANG", "") else "English", help_text="English|Chinese", index=True)
    llm_id = CharField(max_length=128, null=False, help_text="default llm ID")
    tenant_llm_id = IntegerField(null=True, help_text="id in tenant_llm", index=True)

    llm_setting = JSONField(null=False, default={"temperature": 0.1, "top_p": 0.3, "frequency_penalty": 0.7, "presence_penalty": 0.4, "max_tokens": 512})
    prompt_type = CharField(max_length=16, null=False, default="simple", help_text="simple|advanced", index=True)
    prompt_config = JSONField(
        null=False,
        default={"system": "", "prologue": "Hi! I'm your assistant. What can I do for you?", "parameters": [], "empty_response": "Sorry! No relevant content was found in the knowledge base!"},
    )
    meta_data_filter = JSONField(null=True, default={})

    similarity_threshold = FloatField(default=0.2)
    vector_similarity_weight = FloatField(default=0.3)

    top_n = IntegerField(default=6)

    top_k = IntegerField(default=1024)

    do_refer = CharField(max_length=1, null=False, default="1", help_text="it needs to insert reference index into answer or not")

    rerank_id = CharField(max_length=128, null=False, help_text="default rerank model ID")
    tenant_rerank_id = IntegerField(null=True, help_text="id in tenant_llm", index=True)
    kb_ids = JSONField(null=False, default=[])
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    class Meta:
        db_table = "dialog"


class Conversation(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    dialog_id = CharField(max_length=32, null=False, index=True)
    name = CharField(max_length=255, null=True, help_text="conversation name", index=True)
    message = JSONField(null=True)
    reference = JSONField(null=True, default=[])
    user_id = CharField(max_length=255, null=True, help_text="user_id", index=True)

    class Meta:
        db_table = "conversation"


class APIToken(DataBaseModel):
    tenant_id = CharField(max_length=32, null=False, index=True)
    token = CharField(max_length=255, null=False, index=True)
    dialog_id = CharField(max_length=32, null=True, index=True)
    source = CharField(max_length=16, null=True, help_text="none|agent|dialog", index=True)
    beta = CharField(max_length=255, null=True, index=True)

    class Meta:
        db_table = "api_token"
        primary_key = CompositeKey("tenant_id", "token")


class API4Conversation(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    name = CharField(max_length=255, null=True, help_text="conversation name", index=False)
    dialog_id = CharField(max_length=32, null=False, index=True)
    user_id = CharField(max_length=255, null=False, help_text="user_id", index=True)
    exp_user_id = CharField(max_length=255, null=True, help_text="exp_user_id", index=True)
    message = JSONField(null=True)
    reference = JSONField(null=True, default=[])
    tokens = IntegerField(default=0)
    source = CharField(max_length=16, null=True, help_text="none|agent|dialog", index=True)
    dsl = JSONField(null=True, default={})
    duration = FloatField(default=0, index=True)
    round = IntegerField(default=0, index=True)
    thumb_up = IntegerField(default=0, index=True)
    errors = TextField(null=True, help_text="errors")
    version_title = CharField(max_length=255, null=True, help_text="canvas version title when session created", index=False)

    class Meta:
        db_table = "api_4_conversation"


class UserCanvas(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    avatar = TextField(null=True, help_text="avatar base64 string")
    user_id = CharField(max_length=255, null=False, help_text="user_id", index=True)
    title = CharField(max_length=255, null=True, help_text="Canvas title")

    permission = CharField(max_length=16, null=False, help_text="me|team", default="me", index=True)
    release = BooleanField(null=False, help_text="is released", default=False, index=True)
    description = TextField(null=True, help_text="Canvas description")
    canvas_type = CharField(max_length=32, null=True, help_text="Canvas type", index=True)
    canvas_category = CharField(max_length=32, null=False, default="agent_canvas", help_text="Canvas category: agent_canvas|dataflow_canvas", index=True)
    dsl = JSONField(null=True, default={})

    class Meta:
        db_table = "user_canvas"


class CanvasTemplate(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    avatar = TextField(null=True, help_text="avatar base64 string")
    title = JSONField(null=True, default=dict, help_text="Canvas title")
    description = JSONField(null=True, default=dict, help_text="Canvas description")
    canvas_type = CharField(max_length=32, null=True, help_text="Canvas type", index=True)
    canvas_types = ListField(null=True, default=list, help_text="Canvas types")
    canvas_category = CharField(max_length=32, null=False, default="agent_canvas", help_text="Canvas category: agent_canvas|dataflow_canvas", index=True)
    dsl = JSONField(null=True, default={})

    class Meta:
        db_table = "canvas_template"


class UserCanvasVersion(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    user_canvas_id = CharField(max_length=255, null=False, help_text="user_canvas_id", index=True)

    title = CharField(max_length=255, null=True, help_text="Canvas title")
    description = TextField(null=True, help_text="Canvas description")
    release = BooleanField(null=False, help_text="is released", default=False, index=True)
    dsl = JSONField(null=True, default={})

    class Meta:
        db_table = "user_canvas_version"


class MCPServer(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    name = CharField(max_length=255, null=False, help_text="MCP Server name")
    tenant_id = CharField(max_length=32, null=False, index=True)
    url = CharField(max_length=2048, null=False, help_text="MCP Server URL")
    server_type = CharField(max_length=32, null=False, help_text="MCP Server type")
    description = TextField(null=True, help_text="MCP Server description")
    variables = JSONField(null=True, default=dict, help_text="MCP Server variables")
    headers = JSONField(null=True, default=dict, help_text="MCP Server additional request headers")

    class Meta:
        db_table = "mcp_server"


class Search(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    avatar = TextField(null=True, help_text="avatar base64 string")
    tenant_id = CharField(max_length=32, null=False, index=True)
    name = CharField(max_length=128, null=False, help_text="Search name", index=True)
    description = TextField(null=True, help_text="KB description")
    created_by = CharField(max_length=32, null=False, index=True)
    search_config = JSONField(
        null=False,
        default={
            "kb_ids": [],
            "doc_ids": [],
            "similarity_threshold": 0.2,
            "vector_similarity_weight": 0.3,
            "use_kg": False,
            # rerank settings
            "rerank_id": "",
            "top_k": 1024,
            # chat settings
            "summary": False,
            "chat_id": "",
            # Leave it here for reference, don't need to set default values
            "llm_setting": {
                # "temperature": 0.1,
                # "top_p": 0.3,
                # "frequency_penalty": 0.7,
                # "presence_penalty": 0.4,
            },
            "chat_settingcross_languages": [],
            "highlight": False,
            "keyword": False,
            "web_search": False,
            "related_search": False,
            "query_mindmap": False,
        },
    )
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    def __str__(self):
        return self.name

    class Meta:
        db_table = "search"


class PipelineOperationLog(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    document_id = CharField(max_length=32, index=True)
    tenant_id = CharField(max_length=32, null=False, index=True)
    kb_id = CharField(max_length=32, null=False, index=True)
    pipeline_id = CharField(max_length=32, null=True, help_text="Pipeline ID", index=True)
    pipeline_title = CharField(max_length=32, null=True, help_text="Pipeline title", index=True)
    parser_id = CharField(max_length=32, null=False, help_text="Parser ID", index=True)
    document_name = CharField(max_length=255, null=False, help_text="File name")
    document_suffix = CharField(max_length=255, null=False, help_text="File suffix")
    document_type = CharField(max_length=255, null=False, help_text="Document type")
    source_from = CharField(max_length=255, null=False, help_text="Source")
    progress = FloatField(default=0, index=True)
    progress_msg = TextField(null=True, help_text="process message", default="")
    process_begin_at = DateTimeField(null=True, index=True)
    process_duration = FloatField(default=0)
    dsl = JSONField(null=True, default=dict)
    task_type = CharField(max_length=32, null=False, default="")
    operation_status = CharField(max_length=32, null=False, help_text="Operation status")
    avatar = TextField(null=True, help_text="avatar base64 string")
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    class Meta:
        db_table = "pipeline_operation_log"


class Connector(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    tenant_id = CharField(max_length=32, null=False, index=True)
    name = CharField(max_length=128, null=False, help_text="Search name", index=False)
    source = CharField(max_length=128, null=False, help_text="Data source", index=True)
    input_type = CharField(max_length=128, null=False, help_text="poll/event/..", index=True)
    config = JSONField(null=False, default={})
    refresh_freq = IntegerField(default=0, index=False)
    prune_freq = IntegerField(default=0, index=False)
    timeout_secs = IntegerField(default=3600, index=False)
    indexing_start = DateTimeField(null=True, index=True)
    status = CharField(max_length=16, null=True, help_text="schedule", default="schedule", index=True)

    def __str__(self):
        return self.name

    class Meta:
        db_table = "connector"


class Connector2Kb(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    connector_id = CharField(max_length=32, null=False, index=True)
    kb_id = CharField(max_length=32, null=False, index=True)
    auto_parse = CharField(max_length=1, null=False, default="1", index=False)

    class Meta:
        db_table = "connector2kb"


class DateTimeTzField(CharField):
    field_type = 'VARCHAR'

    def db_value(self, value: datetime|None) -> str|None:
        if value is not None:
            if value.tzinfo is not None:
                return value.isoformat()
            else:
                return value.replace(tzinfo=timezone.utc).isoformat()
        return value

    def python_value(self, value: str|None) -> datetime|None:
        if value is not None:
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                import pytz
                return dt.replace(tzinfo=pytz.UTC)
            return dt
        return value


class SyncLogs(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    connector_id = CharField(max_length=32, index=True)
    status = CharField(max_length=128, null=False, help_text="Processing status", index=True)
    from_beginning = CharField(max_length=1, null=True, help_text="", default="0", index=False)
    new_docs_indexed = IntegerField(default=0, index=False)
    total_docs_indexed = IntegerField(default=0, index=False)
    docs_removed_from_index = IntegerField(default=0, index=False)
    error_msg = TextField(null=False, help_text="process message", default="")
    error_count = IntegerField(default=0, index=False)
    full_exception_trace = TextField(null=True, help_text="process message", default="")
    time_started = DateTimeField(null=True, index=True)
    poll_range_start = DateTimeTzField(max_length=255, null=True, index=True)
    poll_range_end = DateTimeTzField(max_length=255, null=True, index=True)
    kb_id = CharField(max_length=32, null=False, index=True)

    class Meta:
        db_table = "sync_logs"


class EvaluationDataset(DataBaseModel):
    """Ground truth dataset for RAG evaluation"""
    id = CharField(max_length=32, primary_key=True)
    tenant_id = CharField(max_length=32, null=False, index=True, help_text="tenant ID")
    name = CharField(max_length=255, null=False, index=True, help_text="dataset name")
    description = TextField(null=True, help_text="dataset description")
    kb_ids = JSONField(null=False, help_text="knowledge base IDs to evaluate against")
    created_by = CharField(max_length=32, null=False, index=True, help_text="creator user ID")
    create_time = BigIntegerField(null=False, index=True, help_text="creation timestamp")
    update_time = BigIntegerField(null=False, help_text="last update timestamp")
    status = IntegerField(null=False, default=1, help_text="1=valid, 0=invalid")

    class Meta:
        db_table = "evaluation_datasets"


class EvaluationCase(DataBaseModel):
    """Individual test case in an evaluation dataset"""
    id = CharField(max_length=32, primary_key=True)
    dataset_id = CharField(max_length=32, null=False, index=True, help_text="FK to evaluation_datasets")
    question = TextField(null=False, help_text="test question")
    reference_answer = TextField(null=True, help_text="optional ground truth answer")
    relevant_doc_ids = JSONField(null=True, help_text="expected relevant document IDs")
    relevant_chunk_ids = JSONField(null=True, help_text="expected relevant chunk IDs")
    metadata = JSONField(null=True, help_text="additional context/tags")
    create_time = BigIntegerField(null=False, help_text="creation timestamp")

    class Meta:
        db_table = "evaluation_cases"


class EvaluationRun(DataBaseModel):
    """A single evaluation run"""
    id = CharField(max_length=32, primary_key=True)
    dataset_id = CharField(max_length=32, null=False, index=True, help_text="FK to evaluation_datasets")
    dialog_id = CharField(max_length=32, null=False, index=True, help_text="dialog configuration being evaluated")
    name = CharField(max_length=255, null=False, help_text="run name")
    config_snapshot = JSONField(null=False, help_text="dialog config at time of evaluation")
    metrics_summary = JSONField(null=True, help_text="aggregated metrics")
    status = CharField(max_length=32, null=False, default="PENDING", help_text="PENDING/RUNNING/COMPLETED/FAILED")
    created_by = CharField(max_length=32, null=False, index=True, help_text="user who started the run")
    create_time = BigIntegerField(null=False, index=True, help_text="creation timestamp")
    complete_time = BigIntegerField(null=True, help_text="completion timestamp")

    class Meta:
        db_table = "evaluation_runs"


class EvaluationResult(DataBaseModel):
    """Result for a single test case in an evaluation run"""
    id = CharField(max_length=32, primary_key=True)
    run_id = CharField(max_length=32, null=False, index=True, help_text="FK to evaluation_runs")
    case_id = CharField(max_length=32, null=False, index=True, help_text="FK to evaluation_cases")
    generated_answer = TextField(null=False, help_text="generated answer")
    retrieved_chunks = JSONField(null=False, help_text="chunks that were retrieved")
    metrics = JSONField(null=False, help_text="all computed metrics")
    execution_time = FloatField(null=False, help_text="response time in seconds")
    token_usage = JSONField(null=True, help_text="prompt/completion tokens")
    create_time = BigIntegerField(null=False, help_text="creation timestamp")

    class Meta:
        db_table = "evaluation_results"


class Memory(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    name = CharField(max_length=128, null=False, index=False, help_text="Memory name")
    avatar = TextField(null=True, help_text="avatar base64 string")
    tenant_id = CharField(max_length=32, null=False, index=True)
    memory_type = IntegerField(null=False, default=1, index=True, help_text="Bit flags (LSB->MSB): 1=raw, 2=semantic, 4=episodic, 8=procedural. E.g., 5 enables raw + episodic.")
    storage_type = CharField(max_length=32, default='table', null=False, index=True, help_text="table|graph")
    embd_id = CharField(max_length=128, null=False, index=False, help_text="embedding model ID")
    tenant_embd_id = IntegerField(null=True, help_text="id in tenant_llm", index=True)
    llm_id = CharField(max_length=128, null=False, index=False, help_text="chat model ID")
    tenant_llm_id = IntegerField(null=True, help_text="id in tenant_llm", index=True)
    permissions = CharField(max_length=16, null=False, index=True, help_text="me|team", default="me")
    description = TextField(null=True, help_text="description")
    memory_size = IntegerField(default=5242880, null=False, index=False)
    forgetting_policy = CharField(max_length=32, null=False, default="FIFO", index=False, help_text="LRU|FIFO")
    temperature = FloatField(default=0.5, index=False)
    system_prompt = TextField(null=True, help_text="system prompt", index=False)
    user_prompt = TextField(null=True, help_text="user prompt", index=False)

    class Meta:
        db_table = "memory"

class SystemSettings(DataBaseModel):
    name = CharField(max_length=128, primary_key=True)
    source = CharField(max_length=32, null=False, index=False)
    data_type = CharField(max_length=32, null=False, index=False)
    value = TextField(null=False, help_text="Configuration value (JSON, string, etc.)")
    class Meta:
        db_table = "system_settings"


# ────────────────────────────────────────────────────────────────────────────
# Agent v2（Claude Agent SDK 驱动的 Agent 运行时）— 表前缀统一 agent_v2_
# 详见 DESIGN.md 第四节
# ────────────────────────────────────────────────────────────────────────────
class AgentV2Session(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    tenant_id = CharField(max_length=32, null=False, index=True)
    user_id = CharField(max_length=255, null=True, index=True, help_text="creator user_id")
    name = CharField(max_length=255, null=True, index=True, help_text="session display name")
    kb_ids = JSONField(null=False, default=[], help_text="KBs this session can query")
    tool_names = JSONField(null=True, default=None, help_text="enabled tool whitelist; null = all registered")
    system_prompt = TextField(null=True, default="", help_text="system prompt")
    model_config_json = JSONField(
        null=False,
        default={"model": "claude-sonnet-4-5", "base_url": None, "auth_token_id": None},
        help_text="ModelConfig serialized; auth_token_id references tenant's LLM config",
    )
    max_turns = IntegerField(default=20)
    max_budget_usd = FloatField(null=True, default=1.0)
    status = CharField(
        max_length=16, null=False, default="active", index=True,
        help_text="active | archived | deleted",
    )

    # Phase 2.5.1 — citation validator (nullable → 向后兼容老 session)
    citation_enforce_level = CharField(
        max_length=16, null=True, default="warn",
        help_text="off | warn | strict — 答复里 [N] 脚注 / 数字型断言的校验级别",
    )
    citation_numeric_strict = IntegerField(
        null=True, default=1,
        help_text="1 = 检查数字 / 金额 / 年限 / 日期必须有原文支撑；0 = 仅检查 [N] 映射",
    )

    # Phase 2.5.2 — 真正的多轮上下文（nullable → 向后兼容老 session）
    history_turn_limit = IntegerField(
        null=True, default=10,
        help_text="每次调 Agent 时回看多少轮历史（user+assistant 各算一条）",
    )
    summary_text = LongTextField(
        null=True, default="",
        help_text="compact 后的历史摘要；下一轮会拼进 prompt 替代旧消息",
    )
    summary_until_seq = IntegerField(
        null=True, default=0,
        help_text="摘要已覆盖到第几条消息（按 create_time 排序的 1-based 下标）",
    )

    # Phase 2.6 v0.4 — real plan gating（nullable → 向后兼容老 session）
    #
    # ``pending_plan_status`` 的合法值：
    #   - NULL / ""       — 无待审批计划（默认）
    #   - "waiting"       — submit_plan 已 emit，等用户回复
    #   - "approved"      — 用户回复 [plan approved]，下一轮的写工具可以直接跑
    #   - "rejected"      — 用户回复 [plan rejected]，写工具继续被拒
    #   - "request_changes" — 用户要求调整；agent 应 re-plan
    #
    # ``pending_plan_id`` 是 submit_plan 返回的 pending_id（uuid hex，无 FK 关系）。
    # 只要 pending_plan_status 不是 NULL / approved，@require_kb_write 就会拒绝。
    pending_plan_id = CharField(
        max_length=32, null=True, default=None, index=True,
        help_text="Current submit_plan pending_id awaiting user decision.",
    )
    pending_plan_status = CharField(
        max_length=24, null=True, default=None, index=True,
        help_text="waiting | approved | rejected | request_changes | null",
    )
    pending_plan_submitted_at = BigIntegerField(
        null=True, default=None,
        help_text="submit_plan 提交时的 ms timestamp；用于 TTL 过期判断",
    )

    # Phase 2.6 v0.6 — 保存 submit_plan 的**完整 payload**（title / steps /
    # affected_resources / risk_level / reversible / reversible_hint / ...），
    # 这样下一轮 Agent 批准后能用新工具 `get_pending_plan` 读回原计划逐步执行，
    # 而不是只能从 tool_call 历史里间接重建（见 `PLAN-doc-ops.md §G7`）。
    pending_plan_body = JSONField(
        null=True, default=None,
        help_text="Full submit_plan payload; cleared together with pending_plan_status",
    )

    class Meta:
        db_table = "agent_v2_session"


class AgentV2Message(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    session_id = CharField(max_length=32, null=False, index=True)
    role = CharField(max_length=16, null=False, index=True, help_text="user | assistant | system")
    content = LongTextField(null=True, default="", help_text="main text body")
    thinking = LongTextField(null=True, default="", help_text="extended thinking text (optional)")
    tool_call_ids = JSONField(null=False, default=[], help_text="tool_call ids triggered by this msg")
    usage = JSONField(null=True, default={}, help_text="token usage for this turn")

    class Meta:
        db_table = "agent_v2_message"


class AgentV2ToolCall(DataBaseModel):
    id = CharField(max_length=64, primary_key=True, help_text="SDK-provided tool_use_id")
    session_id = CharField(max_length=32, null=False, index=True)
    message_id = CharField(max_length=32, null=True, index=True, help_text="parent assistant message")
    tool_name = CharField(max_length=64, null=False, index=True)
    args = JSONField(null=False, default={}, help_text="tool input arguments")
    result = LongTextField(null=True, default="", help_text="tool output (may be JSON text)")
    error = TextField(null=True, default="", help_text="error message if failed")
    status = CharField(
        max_length=16, null=False, default="pending", index=True,
        help_text="pending | success | error | timeout",
    )
    duration_ms = IntegerField(null=True, default=0)
    start_time = BigIntegerField(null=True, index=True)

    class Meta:
        db_table = "agent_v2_tool_call"


# ══════════════════════════════════════════════════════════════════════
# Phase 2.1 — 数据集 RBAC 与审计
# ══════════════════════════════════════════════════════════════════════


class DatasetAccess(DataBaseModel):
    """KB 的显式成员角色；覆盖 `knowledgebase.permission` 的默认行为。

    映射关系：
      - 没有记录 + kb.permission='me'  → 仅创建者可见
      - 没有记录 + kb.permission='team' → 同 tenant 用户视为隐式 VIEWER
      - 有记录 → 以记录中的 role 为准（显式授权永远覆盖默认）
    """

    id = CharField(max_length=32, primary_key=True)
    kb_id = CharField(max_length=32, null=False, index=True)
    user_id = CharField(max_length=255, null=False, index=True)
    role = CharField(
        max_length=16, null=False, index=True,
        help_text="owner | admin | contributor | viewer",
    )
    granted_by = CharField(max_length=255, null=True, help_text="who added this member")

    class Meta:
        db_table = "dataset_access"
        indexes = (
            (("kb_id", "user_id"), True),  # unique
        )


class AccessAuditLog(DataBaseModel):
    """访问审计日志：所有 deny 必须记录；allow 可以按需记录."""

    id = CharField(max_length=32, primary_key=True)
    user_id = CharField(max_length=255, null=True, index=True, help_text="nullable for bots / anonymous")
    tenant_id = CharField(max_length=32, null=False, index=True)
    action = CharField(max_length=32, null=False, index=True,
                       help_text="kb.create|kb.read|kb.update|kb.delete|kb.share|kb.retrieve|kb.ingest|bot.receive|bot.reply|subagent.spawn|...")
    resource_type = CharField(max_length=32, null=False, index=True,
                              help_text="knowledgebase | document | agent_v2_session | bot_channel | subagent_trace")
    resource_id = CharField(max_length=64, null=True, index=True)
    result = CharField(max_length=16, null=False, help_text="allow | deny")
    reason = CharField(max_length=255, null=True)
    metadata = JSONField(null=True, default={})
    ip = CharField(max_length=45, null=True)
    user_agent = TextField(null=True)

    class Meta:
        db_table = "access_audit_log"


# ══════════════════════════════════════════════════════════════════════
# Phase 2.2 — IM 机器人渠道（飞书/钉钉/企微）
# ══════════════════════════════════════════════════════════════════════


class BotChannel(DataBaseModel):
    """IM 机器人通道配置（飞书/钉钉/企微/...）。

    config_json 按 channel_type 不同结构（飞书：app_id / app_secret /
    encrypt_key / verification_token / api_base）。app_secret / encrypt_key /
    verification_token 由 BotChannelService 做应用层字段加密，DB 中形如
    ``enc:v1:<base64>``；优先使用 RAGFLOW_BOT_CHANNEL_SECRET_KEY，未配置时
    回退 settings.SECRET_KEY。
    """

    id = CharField(max_length=32, primary_key=True)
    tenant_id = CharField(max_length=32, null=False, index=True)
    channel_type = CharField(
        max_length=16, null=False, index=True,
        help_text="feishu | dingtalk | wecom",
    )
    account_id = CharField(
        max_length=64, null=False, index=True,
        help_text="该平台下的机器人实例 ID（自定义、URL 路径用），同租户内唯一",
    )
    name = CharField(max_length=128, null=False)
    config_json = JSONField(
        null=False, default={},
        help_text="平台特定凭据/参数（飞书：{app_id, app_secret, encrypt_key, verification_token, api_base}）",
    )
    default_kb_ids = JSONField(
        null=False, default=[], help_text="新会话默认绑定的知识库",
    )
    default_agent_template_id = CharField(
        max_length=32, null=True, help_text="agent_v2 templates.py 里的模板 id",
    )
    default_model_config_json = JSONField(
        null=True, default=None,
        help_text="模型配置（同 agent_v2_session.model_config_json shape）",
    )
    default_system_prompt = TextField(
        null=True, default="",
        help_text="bot 新会话的 system prompt；为空时用模板默认",
    )
    session_scope = CharField(
        max_length=32, null=False, default="group_sender",
        help_text="dm | group | group_sender | group_topic | group_topic_sender",
    )
    enabled = IntegerField(null=False, default=1, index=True, help_text="1=启用 0=停用")

    class Meta:
        db_table = "bot_channel"
        indexes = (
            (("channel_type", "account_id"), True),  # unique
        )


class TenantQuota(DataBaseModel):
    """Phase 3.1b — 每租户的配额设置。

    每个 tenant 最多一条。首次被查询时若不存在则返回默认值（不自动建行，
    避免未激活 tenant 污染表）。实际修改由 super-admin 或系统级迁移写入。
    """

    id = CharField(max_length=32, primary_key=True)
    tenant_id = CharField(max_length=32, null=False, unique=True, index=True)

    # 资源上限（0 或负 = 无限）
    kb_max = IntegerField(null=False, default=50, help_text="知识库数量上限")
    doc_max = IntegerField(null=False, default=10_000, help_text="文档总数上限")
    token_month_max = BigIntegerField(
        null=False, default=50_000_000, help_text="月累计 Token 上限（in+out）",
    )
    api_rps_max = IntegerField(null=False, default=20, help_text="APIToken 每秒请求上限")
    bot_message_day_max = IntegerField(
        null=False, default=5_000, help_text="单日所有机器人累计消息上限",
    )
    subagent_day_max = IntegerField(
        null=False, default=2_000, help_text="单日 spawn_subagent 调用次数上限",
    )
    hard_enforce = IntegerField(
        null=False, default=0,
        help_text="0 = 只记审计不阻塞；1 = 超额直接 429 / 403",
    )

    class Meta:
        db_table = "tenant_quota"


class TenantUsageDaily(DataBaseModel):
    """Phase 3.1b — 每租户每天的用量滚动记账。

    统计口径（按租户日历日，服务器时区）：
      - token_in / token_out：Agent v2 session 的 LLM usage（含子 Agent）
      - cost_usd：LLM 成本估算（SDK 带的 total_cost_usd 累加）
      - api_requests：走 APIToken 的外部调用
      - bot_messages：webhook 接收到的入站消息
      - subagent_spawns：spawn_subagent 工具调用次数
    """

    id = CharField(max_length=32, primary_key=True)
    tenant_id = CharField(max_length=32, null=False, index=True)
    date_ymd = IntegerField(null=False, index=True, help_text="YYYYMMDD (服务器时区)")
    token_in = BigIntegerField(null=False, default=0)
    token_out = BigIntegerField(null=False, default=0)
    cost_usd = FloatField(null=False, default=0.0)
    api_requests = IntegerField(null=False, default=0)
    bot_messages = IntegerField(null=False, default=0)
    subagent_spawns = IntegerField(null=False, default=0)

    class Meta:
        db_table = "tenant_usage_daily"
        indexes = (
            (("tenant_id", "date_ymd"), True),
        )


class AgentV2SubagentTrace(DataBaseModel):
    """Multi-Agent（Phase 2.3）子 Agent 执行轨迹。

    父 Agent 每次调用 ``spawn_subagent`` 工具都写一条：
      - 派出时 status=running 并写入 description/prompt/allowed_tools
      - 结束时更新 status / result_preview / token_usage / cost / duration
      - 前端工具调用卡片可点开这条记录展示子任务细节
    """

    id = CharField(max_length=32, primary_key=True)
    parent_session_id = CharField(max_length=32, null=False, index=True)
    parent_tool_call_id = CharField(
        max_length=64, null=False, index=True,
        help_text="父调 spawn_subagent 的 tool_use_id (=MCP tool call ID)",
    )
    description = CharField(max_length=255, null=False, default="",
                            help_text="任务短标题（UI 上显示）")
    prompt = TextField(null=True, default="", help_text="给子 Agent 的完整 prompt")
    allowed_tools = JSONField(null=False, default=[],
                              help_text="子可用工具白名单；空 = 继承父（除 spawn_subagent）")
    max_turns = IntegerField(default=10)
    max_budget_usd = FloatField(null=True, default=0.3)
    status = CharField(
        max_length=16, null=False, default="running", index=True,
        help_text="running | success | error | truncated | cancelled",
    )
    result_preview = LongTextField(null=True, default="",
                                   help_text="最终 assistant 文本前 4 KB")
    error = TextField(null=True, default="")
    token_usage_json = JSONField(null=True, default=None)
    cost_usd = FloatField(null=True, default=None)
    duration_ms = IntegerField(null=True, default=None)
    start_time = BigIntegerField(null=False, index=True)
    end_time = BigIntegerField(null=True)

    class Meta:
        db_table = "agent_v2_subagent_trace"


class BotConversationMap(DataBaseModel):
    """IM 端会话标识 ↔ Agent v2 session 的持久映射。

    保证同一 IM 会话（按 channel_type / account_id / conversation_key 唯一）
    复用同一个 agent session，跨重启不丢上下文。
    """

    id = CharField(max_length=32, primary_key=True)
    channel_type = CharField(max_length=16, null=False, index=True)
    account_id = CharField(max_length=64, null=False, index=True)
    conversation_key = CharField(
        max_length=255, null=False, index=True,
        help_text="平台特定的会话路由键（如飞书的 chat_id[:topic:..[:sender:..]]）",
    )
    agent_session_id = CharField(max_length=32, null=False, index=True)
    im_user_id = CharField(max_length=128, null=True, help_text="open_id / userid 快照")
    im_user_name = CharField(max_length=128, null=True, help_text="昵称快照")
    last_activity_ms = BigIntegerField(null=False, index=True, default=0)

    class Meta:
        db_table = "bot_conversation_map"
        indexes = (
            (("channel_type", "account_id", "conversation_key"), True),  # unique
        )


# ══════════════════════════════════════════════════════════════════════
# Phase 3.2 — Agent Trigger（Cron + 手动 / Webhook）
# ══════════════════════════════════════════════════════════════════════


class AgentTrigger(DataBaseModel):
    """定时 / 手动触发的 Agent v2 执行配置。

    支持两种触发方式（``trigger_type``）：
      - ``cron``    : 用 ``cron_expr``（5 段标准 crontab）按计划跑
      - ``manual``  : 只接受 UI 点「立即运行」按钮，或 API 直接调
      （未来 Phase 3.2.2 加 ``webhook`` 类型；结构已预留）

    执行结果的投递方式（``delivery_kind``）：
      - ``audit_only``  : 只记 trigger_run 表，不往外发
      - ``feishu_bot``  : 往 ``delivery_config.bot_channel_id`` 指向的飞书机器人的
                          ``delivery_config.chat_id`` 发消息
    """

    id = CharField(max_length=32, primary_key=True)
    tenant_id = CharField(max_length=32, null=False, index=True)
    name = CharField(max_length=128, null=False, help_text="UI 显示名")
    description = TextField(null=True, default="")

    trigger_type = CharField(max_length=16, null=False, index=True, default="cron",
                             help_text="cron | manual | webhook (reserved)")
    cron_expr = CharField(max_length=64, null=True, default=None,
                          help_text="5-field crontab, eg '0 9 * * *'（仅 type=cron）")
    timezone = CharField(max_length=64, null=True, default="Asia/Shanghai")

    agent_session_id = CharField(
        max_length=32, null=False, index=True,
        help_text="运行时用哪个 Agent v2 session 的配置（kb_ids / system_prompt / model）",
    )
    prompt = TextField(null=False, default="",
                       help_text="每次触发发给 Agent 的用户消息（v1 不支持变量替换）")
    max_turns = IntegerField(null=False, default=20)
    max_budget_usd = FloatField(null=True, default=1.0)

    delivery_kind = CharField(max_length=24, null=False, default="audit_only",
                              help_text="audit_only | feishu_bot")
    delivery_config = JSONField(null=False, default={},
                                help_text="投递参数（bot_channel_id / chat_id 等）")

    enabled = IntegerField(null=False, default=1, index=True)

    next_run_at = BigIntegerField(null=True, index=True,
                                  help_text="下次到期毫秒时间戳；worker 就读这列决定跑谁")
    last_run_at = BigIntegerField(null=True)
    last_run_status = CharField(max_length=16, null=True)
    last_run_error = TextField(null=True)
    created_by = CharField(max_length=255, null=True)

    class Meta:
        db_table = "agent_trigger"


class AgentTriggerRun(DataBaseModel):
    """一次 trigger 执行的历史记录。"""

    id = CharField(max_length=32, primary_key=True)
    trigger_id = CharField(max_length=32, null=False, index=True)
    tenant_id = CharField(max_length=32, null=False, index=True)

    kicked_by = CharField(
        max_length=32, null=False, default="scheduler",
        help_text="scheduler | manual | webhook",
    )

    status = CharField(
        max_length=16, null=False, default="running", index=True,
        help_text="running | success | error | timeout | cancelled",
    )
    started_at = BigIntegerField(null=False, index=True)
    completed_at = BigIntegerField(null=True)
    duration_ms = IntegerField(null=True)

    result_preview = LongTextField(null=True, default="",
                                   help_text="最终 assistant 文本前 4 KB")
    error = TextField(null=True, default="")
    token_usage_json = JSONField(null=True, default=None)
    cost_usd = FloatField(null=True, default=None)
    delivery_status = CharField(max_length=16, null=True,
                                help_text="skipped | success | error")
    delivery_error = TextField(null=True)

    class Meta:
        db_table = "agent_trigger_run"


def alter_db_add_column(migrator, table_name, column_name, column_type):
    try:
        migrate(migrator.add_column(table_name, column_name, column_type))
    except OperationalError as ex:
        error_codes = [1060]
        error_messages = ['Duplicate column name']

        should_skip_error = (
                (hasattr(ex, 'args') and ex.args and ex.args[0] in error_codes) or
                (str(ex) in error_messages)
        )

        if not should_skip_error:
            logging.critical(f"Failed to add {settings.DATABASE_TYPE.upper()}.{table_name} column {column_name}, operation error: {ex}")

    except Exception as ex:
        logging.critical(f"Failed to add {settings.DATABASE_TYPE.upper()}.{table_name} column {column_name}, error: {ex}")
        pass

def alter_db_column_type(migrator, table_name, column_name, new_column_type):
    try:
        migrate(migrator.alter_column_type(table_name, column_name, new_column_type))
    except Exception as ex:
        logging.critical(f"Failed to alter {settings.DATABASE_TYPE.upper()}.{table_name} column {column_name} type, error: {ex}")
        pass

def alter_db_rename_column(migrator, table_name, old_column_name, new_column_name):
    try:
        migrate(migrator.rename_column(table_name, old_column_name, new_column_name))
    except Exception:
        # rename fail will lead to a weired error.
        # logging.critical(f"Failed to rename {settings.DATABASE_TYPE.upper()}.{table_name} column {old_column_name} to {new_column_name}, error: {ex}")
        pass

def migrate_add_unique_email(migrator):
    """Deduplicates user emails and add UNIQUE constraint to email column (idempotent)"""
    # step 0: check existing index state on user.email and prepare for unique constraint
    try:
        if settings.DATABASE_TYPE.upper() == "POSTGRES":
            cursor = DB.execute_sql("""
                SELECT COUNT(*)
                FROM pg_indexes
                WHERE tablename = 'user'
                  AND indexname = 'user_email'
            """)
            result = cursor.fetchone()
            if result and result[0] > 0:
                logging.info("UNIQUE index on user.email already exists, skipping migration")
                return
        else:
            # Fetch the first index on email: tells us both the name and whether it's unique.
            # non_unique=0 means unique, non_unique=1 means non-unique.
            cursor = DB.execute_sql("""
                SELECT index_name, non_unique
                FROM information_schema.statistics
                WHERE table_schema = DATABASE()
                  AND table_name = 'user'
                  AND column_name = 'email'
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                index_name, non_unique = row
                if non_unique == 0:
                    logging.info("UNIQUE index on user.email already exists, skipping migration")
                    return
                # Non-unique index exists (e.g. from old peewee index=True); drop it so
                # the upcoming ADD UNIQUE INDEX does not hit MySQL error 1061 "Duplicate key name".
                DB.execute_sql(f"ALTER TABLE `user` DROP INDEX `{index_name}`")
                logging.info(f"Dropped non-unique index '{index_name}' on user.email before adding unique index")
    except Exception as ex:
        logging.warning(f"Failed to check/prepare email index on user table: {ex}, continuing with migration")

    # step 1: rename duplicate rows so the UNIQUE constraint can be applied
    try:
        duplicates = User.select(User.email).group_by(User.email).having(fn.COUNT(User.id) > 1).tuples()
        for (dup_email,) in duplicates:
            # Keep the superuser row, or the oldest row if there is no superuser
            rows = list(
                User
                    .select(User.id)
                    .where(User.email == dup_email)
                    .order_by(User.is_superuser.desc(), User.create_time.asc())
                    .tuples()
            )
            for (uid,) in rows[1:]:
                new_email = f"{dup_email}_DUPLICATE_{uid[:8]}"
                User.update(email=new_email).where(User.id == uid).execute()
                logging.warning("Renamed duplicate user %s email to %s during migration", uid, new_email)
    except Exception as ex:
        logging.critical("Failed to deduplicate user.email before adding UNIQUE constraint: %s", ex)
        return

    # step 2: add UNIQUE index via migrator
    try:
        migrate(migrator.add_index("user", ("email",), unique=True))
    except (OperationalError, ProgrammingError) as ex:
        msg = str(ex)
        # MySQL 1061 "Duplicate key name" or PostgreSQL "already exists" -> already migrated
        if "1061" in msg or "Duplicate key name" in msg or "already exists" in msg.lower():
            pass
        else:
            logging.critical("Failed to add UNIQUE constraint on user.email: %s", ex)
    except Exception as ex:
        logging.critical("Failed to add UNIQUE constraint on user.email: %s", ex)



def update_tenant_llm_to_id_primary_key():
    """Add ID and set to primary key step by step."""
    if settings.DATABASE_TYPE.upper() == "POSTGRES":
        _update_tenant_llm_to_id_primary_key_postgres()
    else:
        _update_tenant_llm_to_id_primary_key_mysql()


def _update_tenant_llm_to_id_primary_key_mysql():
    """MySQL implementation: Add ID column and set as AUTO_INCREMENT primary key."""
    try:
        with DB.atomic():
            # 0. Check if 'id' column already exists
            cursor = DB.execute_sql("""
                            SELECT COLUMN_NAME
                            FROM INFORMATION_SCHEMA.COLUMNS
                            WHERE TABLE_SCHEMA = DATABASE()
                            AND TABLE_NAME = 'tenant_llm'
                            AND COLUMN_NAME = 'id'
                        """)
            if cursor.rowcount > 0:
                return

            # 1. Add nullable column
            DB.execute_sql("ALTER TABLE tenant_llm ADD COLUMN temp_id INT NULL")

            # 2. Set ID using MySQL user variables
            DB.execute_sql("SET @row = 0;")
            DB.execute_sql("UPDATE tenant_llm SET temp_id = (@row := @row + 1) ORDER BY tenant_id, llm_factory, llm_name;")

            # 3. Drop old primary key
            DB.execute_sql("ALTER TABLE tenant_llm DROP PRIMARY KEY")

            # 4. Update ID column to primary key with AUTO_INCREMENT
            DB.execute_sql("""
            ALTER TABLE tenant_llm
            MODIFY COLUMN temp_id INT NOT NULL AUTO_INCREMENT PRIMARY KEY
            """)

            # 5. Add unique key
            DB.execute_sql("""
                ALTER TABLE tenant_llm
                ADD CONSTRAINT uk_tenant_llm UNIQUE (tenant_id, llm_factory, llm_name)
            """)

            # 6. rename
            DB.execute_sql("ALTER TABLE tenant_llm RENAME COLUMN temp_id TO id")

            logging.info("Successfully updated tenant_llm to id primary key.")

    except Exception as e:
        logging.error(str(e))
        cursor = DB.execute_sql("""
                                    SELECT COLUMN_NAME
                                    FROM INFORMATION_SCHEMA.COLUMNS
                                    WHERE TABLE_SCHEMA = DATABASE()
                                    AND TABLE_NAME = 'tenant_llm'
                                    AND COLUMN_NAME = 'temp_id'
                                """)
        if cursor.rowcount > 0:
            DB.execute_sql("ALTER TABLE tenant_llm DROP COLUMN temp_id")


def _update_tenant_llm_to_id_primary_key_postgres():
    """PostgreSQL implementation: Add SERIAL primary key column to tenant_llm."""
    try:
        with DB.atomic():
            # 0. Check if 'id' column already exists
            cursor = DB.execute_sql("""
                            SELECT column_name
                            FROM information_schema.columns
                            WHERE table_catalog = current_database()
                            AND table_name = 'tenant_llm'
                            AND column_name = 'id'
                        """)
            if cursor.rowcount > 0:
                return

            # 1. Add nullable integer column
            DB.execute_sql("ALTER TABLE tenant_llm ADD COLUMN temp_id INTEGER NULL")

            # 2. Assign sequential row numbers ordered consistently
            DB.execute_sql("""
                UPDATE tenant_llm
                SET temp_id = subq.rn
                FROM (
                    SELECT ctid,
                           ROW_NUMBER() OVER (ORDER BY tenant_id, llm_factory, llm_name) AS rn
                    FROM tenant_llm
                ) AS subq
                WHERE tenant_llm.ctid = subq.ctid
            """)

            # 3. Drop old composite primary key constraint
            cursor = DB.execute_sql("""
                SELECT constraint_name
                FROM information_schema.table_constraints
                WHERE table_catalog = current_database()
                  AND table_name = 'tenant_llm'
                  AND constraint_type = 'PRIMARY KEY'
            """)
            row = cursor.fetchone()
            if row:
                DB.execute_sql(f'ALTER TABLE tenant_llm DROP CONSTRAINT "{row[0]}"')

            # 4. Make temp_id NOT NULL and create a sequence for it
            DB.execute_sql("ALTER TABLE tenant_llm ALTER COLUMN temp_id SET NOT NULL")
            DB.execute_sql("CREATE SEQUENCE IF NOT EXISTS tenant_llm_id_seq")
            DB.execute_sql("""
                SELECT setval('tenant_llm_id_seq', COALESCE((SELECT MAX(temp_id) FROM tenant_llm), 0))
            """)
            DB.execute_sql("ALTER TABLE tenant_llm ALTER COLUMN temp_id SET DEFAULT nextval('tenant_llm_id_seq')")
            DB.execute_sql("ALTER SEQUENCE tenant_llm_id_seq OWNED BY tenant_llm.temp_id")
            DB.execute_sql("ALTER TABLE tenant_llm ADD PRIMARY KEY (temp_id)")

            # 5. Add unique constraint
            DB.execute_sql("""
                ALTER TABLE tenant_llm
                ADD CONSTRAINT uk_tenant_llm UNIQUE (tenant_id, llm_factory, llm_name)
            """)

            # 6. Rename temp_id to id
            DB.execute_sql("ALTER TABLE tenant_llm RENAME COLUMN temp_id TO id")

            logging.info("Successfully updated tenant_llm to id primary key (PostgreSQL).")

    except Exception as e:
        logging.error(str(e))
        cursor = DB.execute_sql("""
                                    SELECT column_name
                                    FROM information_schema.columns
                                    WHERE table_catalog = current_database()
                                    AND table_name = 'tenant_llm'
                                    AND column_name = 'temp_id'
                                """)
        if cursor.rowcount > 0:
            DB.execute_sql("ALTER TABLE tenant_llm DROP COLUMN temp_id")


def migrate_db():
    logging.disable(logging.ERROR)
    migrator = DatabaseMigrator[settings.DATABASE_TYPE.upper()].value(DB)
    alter_db_add_column(migrator, "file", "source_type", CharField(max_length=128, null=False, default="", help_text="where dose this document come from", index=True))
    alter_db_add_column(migrator, "tenant", "rerank_id", CharField(max_length=128, null=False, default="BAAI/bge-reranker-v2-m3", help_text="default rerank model ID"))
    alter_db_add_column(migrator, "dialog", "rerank_id", CharField(max_length=128, null=False, default="", help_text="default rerank model ID"))
    alter_db_column_type(migrator, "dialog", "top_k", IntegerField(default=1024))
    alter_db_add_column(migrator, "tenant_llm", "api_key", CharField(max_length=2048, null=True, help_text="API KEY", index=True))
    alter_db_add_column(migrator, "api_token", "source", CharField(max_length=16, null=True, help_text="none|agent|dialog", index=True))
    alter_db_add_column(migrator, "tenant", "tts_id", CharField(max_length=256, null=True, help_text="default tts model ID", index=True))
    alter_db_add_column(migrator, "api_4_conversation", "source", CharField(max_length=16, null=True, help_text="none|agent|dialog", index=True))
    alter_db_add_column(migrator, "task", "retry_count", IntegerField(default=0))
    alter_db_column_type(migrator, "api_token", "dialog_id", CharField(max_length=32, null=True, index=True))
    alter_db_add_column(migrator, "tenant_llm", "max_tokens", IntegerField(default=8192, index=True))
    alter_db_add_column(migrator, "api_4_conversation", "dsl", JSONField(null=True, default={}))
    alter_db_add_column(migrator, "knowledgebase", "pagerank", IntegerField(default=0, index=False))
    alter_db_add_column(migrator, "api_token", "beta", CharField(max_length=255, null=True, index=True))
    alter_db_add_column(migrator, "task", "digest", TextField(null=True, help_text="task digest", default=""))
    alter_db_add_column(migrator, "task", "chunk_ids", LongTextField(null=True, help_text="chunk ids", default=""))
    alter_db_add_column(migrator, "conversation", "user_id", CharField(max_length=255, null=True, help_text="user_id", index=True))
    alter_db_add_column(migrator, "task", "task_type", CharField(max_length=32, null=False, default=""))
    alter_db_add_column(migrator, "task", "priority", IntegerField(default=0))
    alter_db_add_column(migrator, "user_canvas", "permission", CharField(max_length=16, null=False, help_text="me|team", default="me", index=True))
    alter_db_add_column(migrator, "user_canvas", "release", BooleanField(null=False, help_text="is released", default=False, index=True))
    alter_db_add_column(migrator, "llm", "is_tools", BooleanField(null=False, help_text="support tools", default=False))
    alter_db_add_column(migrator, "mcp_server", "variables", JSONField(null=True, help_text="MCP Server variables", default=dict))
    alter_db_rename_column(migrator, "task", "process_duation", "process_duration")
    alter_db_rename_column(migrator, "document", "process_duation", "process_duration")
    alter_db_add_column(migrator, "document", "suffix", CharField(max_length=32, null=False, default="", help_text="The real file extension suffix", index=True))
    alter_db_add_column(migrator, "api_4_conversation", "errors", TextField(null=True, help_text="errors"))
    alter_db_add_column(migrator, "dialog", "meta_data_filter", JSONField(null=True, default={}))
    alter_db_column_type(migrator, "canvas_template", "title", JSONField(null=True, default=dict, help_text="Canvas title"))
    alter_db_column_type(migrator, "canvas_template", "description", JSONField(null=True, default=dict, help_text="Canvas description"))
    alter_db_add_column(migrator, "user_canvas", "canvas_category", CharField(max_length=32, null=False, default="agent_canvas", help_text="agent_canvas|dataflow_canvas", index=True))
    alter_db_add_column(migrator, "canvas_template", "canvas_category", CharField(max_length=32, null=False, default="agent_canvas", help_text="agent_canvas|dataflow_canvas", index=True))
    alter_db_add_column(migrator, "canvas_template", "canvas_types", ListField(null=True, default=list, help_text="Canvas types"))
    alter_db_add_column(migrator, "knowledgebase", "pipeline_id", CharField(max_length=32, null=True, help_text="Pipeline ID", index=True))
    alter_db_add_column(migrator, "document", "pipeline_id", CharField(max_length=32, null=True, help_text="Pipeline ID", index=True))
    alter_db_add_column(migrator, "knowledgebase", "graphrag_task_id", CharField(max_length=32, null=True, help_text="Gragh RAG task ID", index=True))
    alter_db_add_column(migrator, "knowledgebase", "raptor_task_id", CharField(max_length=32, null=True, help_text="RAPTOR task ID", index=True))
    alter_db_add_column(migrator, "knowledgebase", "graphrag_task_finish_at", DateTimeField(null=True))
    alter_db_add_column(migrator, "knowledgebase", "raptor_task_finish_at", CharField(null=True))
    alter_db_add_column(migrator, "knowledgebase", "mindmap_task_id", CharField(max_length=32, null=True, help_text="Mindmap task ID", index=True))
    alter_db_add_column(migrator, "knowledgebase", "mindmap_task_finish_at", CharField(null=True))
    alter_db_column_type(migrator, "tenant_llm", "api_key", TextField(null=True, help_text="API KEY"))
    alter_db_add_column(migrator, "tenant_llm", "status", CharField(max_length=1, null=False, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True))
    alter_db_add_column(migrator, "connector2kb", "auto_parse", CharField(max_length=1, null=False, default="1", index=False))
    alter_db_add_column(migrator, "llm_factories", "rank", IntegerField(default=0, index=False))
    alter_db_add_column(migrator, "api_4_conversation", "name", CharField(max_length=255, null=True, help_text="conversation name", index=False))
    alter_db_add_column(migrator, "api_4_conversation", "exp_user_id", CharField(max_length=255, null=True, help_text="exp_user_id", index=True))
    # Migrate system_settings.value from CharField to TextField for longer sandbox configs
    alter_db_column_type(migrator, "system_settings", "value", TextField(null=False, help_text="Configuration value (JSON, string, etc.)"))
    alter_db_add_column(migrator, "document", "content_hash", CharField(max_length=32, null=True, help_text="xxhash128 of document content for change detection", default="", index=True))
    update_tenant_llm_to_id_primary_key()
    alter_db_add_column(migrator, "tenant", "tenant_llm_id", IntegerField(null=True, help_text="id in tenant_llm", index=True))
    alter_db_add_column(migrator, "tenant", "tenant_embd_id", IntegerField(null=True, help_text="id in tenant_llm", index=True))
    alter_db_add_column(migrator, "tenant", "tenant_asr_id", IntegerField(null=True, help_text="id in tenant_llm", index=True))
    alter_db_add_column(migrator, "tenant", "tenant_img2txt_id", IntegerField(null=True, help_text="id in tenant_llm", index=True))
    alter_db_add_column(migrator, "tenant", "tenant_rerank_id", IntegerField(null=True, help_text="id in tenant_llm", index=True))
    alter_db_add_column(migrator, "tenant", "tenant_tts_id", IntegerField(null=True, help_text="id in tenant_llm", index=True))
    alter_db_add_column(migrator, "knowledgebase", "tenant_embd_id", IntegerField(null=True, help_text="id in tenant_llm", index=True))
    alter_db_add_column(migrator, "dialog", "tenant_llm_id", IntegerField(null=True, help_text="id in tenant_llm", index=True))
    alter_db_add_column(migrator, "dialog", "tenant_rerank_id", IntegerField(null=True, help_text="id in tenant_llm", index=True))
    alter_db_add_column(migrator, "memory", "tenant_embd_id", IntegerField(null=True, help_text="id in tenant_llm", index=True))
    alter_db_add_column(migrator, "memory", "tenant_llm_id", IntegerField(null=True, help_text="id in tenant_llm", index=True))
    alter_db_add_column(migrator, "user_canvas_version", "release", BooleanField(null=False, help_text="is released", default=False, index=True))
    alter_db_add_column(migrator, "api_4_conversation", "version_title", CharField(max_length=255, null=True, help_text="canvas version title when session created", index=False))
    alter_db_column_type(migrator, "document", "size", BigIntegerField(default=0, index=True))
    alter_db_column_type(migrator, "file", "size", BigIntegerField(default=0, index=True))
    logging.disable(logging.NOTSET)
    # this is after re-enabling logging to allow logging changed user emails
    migrate_add_unique_email(migrator)
