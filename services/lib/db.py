# This module implements wrapper code around an SQLite3 database, to make it
# easier to work with.
#
#   Connor Shugg

import os
import sys
import sqlite3
import openpyxl

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

from lib.config import Config, ConfigField

class DatabaseConfig(Config):
    """Represent a database configuration."""
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("path",          [str],      required=True),
        ]

class Database:
    """Represent a database interface."""
    def __init__(self, config: DatabaseConfig):
        """Initializes a new database object with the provided config."""
        self.config = config
        self.conn = None

    def get_connection(self, reset=False):
        """Retrieves the current connection cached in the object, or creates a new
        one if it doesn't exist.
        """
        # If we don't have a connection, create one.
        if self.conn is None:
            self.conn = sqlite3.connect(self.config.path)

        # If we already have a connection but a reset was requested, close the
        # existing connection and create a new one.
        if self.conn is not None and reset:
            self.conn.close()
            self.conn = sqlite3.connect(self.config.path)

        return self.conn

    def close_connection(self):
        """Closes the object's cached connection."""
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def delete(self):
        """Deletes the databsae file, completely wiping all data and removing it from
        the filesystem.
        """
        self.close_connection()
        if os.path.exists(self.config.path):
            os.remove(self.config.path)

    def execute(self, query: str, do_commit=False):
        """Executes the provided query and returns the result of
        `connection.execute()`.

        If `do_commit` is set to `True`, the transaction will be committed after
        executing the query.
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        result = cursor.execute(query)

        # If requested, commit the transaction.
        if do_commit:
            conn.commit()
        return result

    def table_exists(self, table: str) -> bool:
        """Determines if a table exists in the database."""
        conn = self.get_connection()
        cur = conn.cursor()
        result = cur.execute("SELECT 1 FROM sqlite_master WHERE type == 'table' AND name == '%s';" % table)
        table_exists = result.fetchone() is not None
        return table_exists

    def get_all_table_names(self):
        """Returns a list of all tables present in the database."""
        conn = self.get_connection()
        cur = conn.cursor()
        result = cur.execute("SELECT name FROM sqlite_master WHERE type == 'table';")
        return [row[0] for row in result]

    def get_table_column_names(self, table: str):
        """Returns a list of all column names in the provided table."""
        conn = self.get_connection()
        cur = conn.cursor()
        result = cur.execute("PRAGMA table_info(%s);" % table)
        return [row[1] for row in result]

    def search(self, table: str, condition: str):
        """Performs a search of the database and returns tuples in a list."""
        # If the table doesn't exist, return an empty list
        if not self.table_exists(table):
            return []

        # Build a SELECT command:
        cmd = "SELECT * FROM %s" % table
        if condition is not None and len(condition) > 0:
            cmd += " WHERE %s" % condition

        # Connect, query, and return
        conn = self.get_connection()
        cur = conn.cursor()
        result = cur.execute(cmd)
        return result

    def search_order_by(self,
                        table: str,
                        order_by_column: str,
                        desc: bool = False,
                        limit: int = None):
        """Executes a search using `ORDER BY` to retrieve entries without needing a
        specific condition to identify them.
        """
        # If the table doesn't exist, return an empty list
        if not self.table_exists(table):
            return []

        # Build a SELECT command
        cmd = "SELECT * FROM %s ORDER BY %s" % (table, order_by_column)
        if desc:
            cmd += " DESC"
        if limit is not None:
            cmd += " LIMIT %d" % limit

        # Connect, query, and return
        conn = self.get_connection()
        cur = conn.cursor()
        result = cur.execute(cmd)
        return result

    def table_to_csv(self, table: str, condition: str):
        """Queries the database and returns a table's values, filtered using
        `condition` as a CSV string.
        """
        result = self.search(table, condition)
        return "\n".join([",".join(row) for row in result])

    def export_to_excel(self, path: str, table_names: list[str] = None):
        """Exports all tables present (or only the ones specified in `table_names`)
        in the database to an Excel (spreadsheet) file at the provided path.
        """
        wb = openpyxl.Workbook()

        # If no table names were provided, export all tables.
        if table_names is None:
            table_names = self.get_all_table_names()

        # For each table...
        for table_name in table_names:
            # Create a new sheet with the table's name as the title:
            ws = wb.create_sheet(title=table_name)

            # Retrieve ALL entries in the table:
            result = self.search(table_name, None)

            # Get the column names for the table, and write them to the first
            # row of the sheet.
            column_names = self.get_table_column_names(table_name)
            row_index = 1
            for i, column_name in enumerate(column_names):
                ws.cell(row=row_index, column=i+1).value = column_name
            row_index += 1

            # For each row, write the values to the corresponding cells in the
            # sheet, in the rows directly underneath the column names.
            for i, row in enumerate(result):
                for j, value in enumerate(row):
                    ws.cell(row=i+row_index, column=j+1).value = value

        # Save the workbook to the provided path.
        wb.save(path)

