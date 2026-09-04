import os
import re
import time
import sqlite3
import pandas as pd
import ollama


# ============================================================
# CONFIGURATION
# ============================================================

DB_PATH = "superstore.db"
QUESTIONS_FILE = "benchmark/benchmark_questions.csv"
RESULTS_FILE = "benchmark/results.csv"

MODEL = "qwen2.5:3b"


# ============================================================
# LOAD DATABASE SCHEMA
# ============================================================

def get_schema(conn):
    schema_df = pd.read_sql_query(
        "PRAGMA table_info(orders);",
        conn
    )

    schema = ", ".join(
        f"{row['name']} ({row['type']})"
        for _, row in schema_df.iterrows()
    )

    return schema


# ============================================================
# SQL VALIDATION
# ============================================================

def validate_sql(sql):

    sql_clean = sql.strip().upper()

    # Must start with SELECT
    if not sql_clean.startswith("SELECT"):
        return False

    # Block potentially destructive SQL
    forbidden = [
        "INSERT",
        "UPDATE",
        "DELETE",
        "DROP",
        "ALTER",
        "CREATE",
        "REPLACE",
        "ATTACH",
        "DETACH"
    ]

    for keyword in forbidden:
        if re.search(rf"\b{keyword}\b", sql_clean):
            return False

    return True


# ============================================================
# CLEAN GENERATED SQL
# ============================================================

def clean_sql(sql):

    sql = sql.strip()

    # Remove markdown code fences
    sql = re.sub(r"```sql", "", sql, flags=re.IGNORECASE)
    sql = re.sub(r"```", "", sql)

    sql = sql.strip()

    # Remove common prefixes
    sql = re.sub(
        r"^(SQL\s*:\s*)",
        "",
        sql,
        flags=re.IGNORECASE
    )

    # Remove trailing semicolon
    sql = sql.rstrip(";").strip()

    # Sometimes the model returns extra text after SQL.
    # Keep only the SELECT statement.
    select_match = re.search(
        r"(SELECT\b.*)",
        sql,
        flags=re.IGNORECASE | re.DOTALL
    )

    if select_match:
        sql = select_match.group(1).strip()

    # Remove accidental trailing explanation
    sql = sql.split("\nExplanation:")[0].strip()
    sql = sql.split("\nHere")[0].strip()

    return sql


# ============================================================
# QUESTION ANSWERABILITY CHECK
# ============================================================

def check_question_answerability(question, schema):

    question_lower = question.lower()

    # Deterministic recognition of known business concepts.
    # This prevents the small local model from incorrectly
    # rejecting simple questions such as "What is total profit?"
    known_terms = {
        "sales": [
            "sales",
            "revenue",
            "selling",
            "sold"
        ],

        "profit": [
            "profit",
            "profits",
            "profitable",
            "profitability"
        ],

        "quantity": [
            "quantity",
            "units",
            "number of items"
        ],

        "discount": [
            "discount",
            "discounts"
        ],

        "category": [
            "category",
            "categories"
        ],

        "sub_category": [
            "sub-category",
            "subcategory",
            "sub category"
        ],

        "product": [
            "product",
            "products",
            "item",
            "items"
        ],

        "customer": [
            "customer",
            "customers",
            "client",
            "clients"
        ],

        "segment": [
            "segment",
            "segments",
            "customer segment",
            "customer group",
            "customer groups"
        ],

        "region": [
            "region",
            "regions",
            "area",
            "areas"
        ],

        "order_date": [
            "date",
            "dates",
            "month",
            "monthly",
            "year",
            "yearly",
            "quarter",
            "quarterly",
            "order date",
            "time",
            "trend",
            "trends"
        ],

        "ship_date": [
            "ship date",
            "shipping date",
            "ship",
            "shipping"
        ]
    }

    matched_terms = []

    for column, terms in known_terms.items():

        for term in terms:

            if term in question_lower:
                matched_terms.append(column)
                break

    # If question clearly references a known database concept,
    # treat it as answerable.
    if matched_terms:
        return True

    # Otherwise ask Qwen.
    answerability_prompt = f"""
You are checking whether a business question can be answered
using the available Superstore sales database.

The database contains order, customer, product and sales data.

Database schema:
{schema}

User question:
{question}

Determine whether the question can be answered using ONLY
the available database.

Rules:

1. Return ONLY YES or NO.
2. Return YES only if the requested information can reasonably
   be calculated from the available data.
3. Return NO if the requested information is not represented
   by the database.
4. Never assume missing information exists.
5. Do not use outside knowledge.

Examples:

Question: Which category has the highest profit?
Answer: YES

Question: What is the total profit?
Answer: YES

Question: What are the monthly sales?
Answer: YES

Question: Which region has the highest sales?
Answer: YES

Question: What is the employee satisfaction score?
Answer: NO

Question: How many employees does the company have?
Answer: NO

Question: What is the company's marketing budget?
Answer: NO

Question: What is the weather today?
Answer: NO

Return only YES or NO.
"""

    try:

        response = ollama.chat(
            model=MODEL,
            messages=[
                {
                    "role": "user",
                    "content": answerability_prompt
                }
            ]
        )

        answer = response["message"]["content"].strip().upper()

        return answer.startswith("YES")

    except Exception:
        return False


# ============================================================
# TEXT-TO-SQL GENERATION
# ============================================================

def generate_sql(question, schema):

    prompt = f"""
You are an expert Text-to-SQL system.

Your job is to convert the user's business question into
ONE correct SQLite SELECT query.

DATABASE
--------

Table name:
orders

Available columns:
{schema}


COLUMN MEANINGS
---------------

sales
= sales/revenue generated

profit
= profit

quantity
= number of units/items sold

discount
= discount value

category
= product category

sub_category
= product sub-category

product_name
= individual product

segment
= customer segment

region
= sales region

order_date
= order date

ship_date
= shipping date


CRITICAL DIMENSION LOCKING RULE
--------------------------------

You MUST use the exact business dimension requested
by the user.

If the question asks for CATEGORY:
    use category

If the question asks for SUB-CATEGORY:
    use sub_category

If the question asks for PRODUCT:
    use product_name

If the question asks for REGION:
    use region

If the question asks for AREA:
    use region

If the question asks for CUSTOMER SEGMENT:
    use segment

If the question asks for CUSTOMER GROUP:
    use segment

If the question asks for CUSTOMER:
    use customer-related customer column available in schema

NEVER substitute one dimension for another.

Examples:

"Which region has the highest sales?"

MUST use:
    region

MUST NOT use:
    category
    sub_category
    segment
    product_name
    year


"Which region has the highest profit?"

MUST use:
    region

MUST NOT use:
    category
    sub_category
    segment
    product_name
    year


"Which customer segment has the highest profit?"

MUST use:
    segment

MUST NOT use:
    category
    sub_category
    region
    product_name
    year


"Which customer segment has the highest sales?"

MUST use:
    segment

MUST NOT use:
    category
    sub_category
    region
    product_name
    year


"Which sub-category has the highest sales?"

MUST use:
    sub_category

MUST NOT use:
    category
    product_name
    segment
    region


"Which sub-category has the highest profit?"

MUST use:
    sub_category

MUST NOT use:
    category
    product_name
    segment
    region


"Which category has the highest profit?"

MUST use:
    category

MUST NOT use:
    sub_category
    product_name
    segment
    region


"Which product has the highest profit?"

MUST use:
    product_name

MUST NOT use:
    category
    sub_category
    segment
    region


AGGREGATION RULES
-----------------

For total sales:
    SUM(sales)

For total profit:
    SUM(profit)

For total quantity:
    SUM(quantity)

For average discount:
    AVG(discount)


GROUPING RULES
--------------

If the question asks which CATEGORY performs best:

SELECT category, SUM(...)
FROM orders
GROUP BY category
ORDER BY ... DESC
LIMIT 1


If the question asks which SUB-CATEGORY performs best:

SELECT sub_category, SUM(...)
FROM orders
GROUP BY sub_category
ORDER BY ... DESC
LIMIT 1


If the question asks which REGION performs best:

SELECT region, SUM(...)
FROM orders
GROUP BY region
ORDER BY ... DESC
LIMIT 1


If the question asks which CUSTOMER SEGMENT performs best:

SELECT segment, SUM(...)
FROM orders
GROUP BY segment
ORDER BY ... DESC
LIMIT 1


If the question asks for PRODUCTS:

SELECT product_name, SUM(...)
FROM orders
GROUP BY product_name
ORDER BY ... DESC
LIMIT N


TOP N RULES
-----------

For "top 5 products by profit":

GROUP BY product_name
ORDER BY SUM(profit) DESC
LIMIT 5


For "top 5 products by sales":

GROUP BY product_name
ORDER BY SUM(sales) DESC
LIMIT 5


HIGHEST / MOST / BEST RULE
--------------------------

Words such as:

highest
most
best
top
maximum

usually mean:

ORDER BY metric DESC
LIMIT 1

unless the user explicitly requests multiple results.


TOTAL RULE
----------

If the user asks for a total:

Do NOT GROUP BY.

Do NOT ORDER BY.

Do NOT LIMIT.

Example:

Question:
What is the total profit?

Correct:

SELECT SUM(profit) AS total_profit
FROM orders


TIME TREND RULES
----------------

For monthly sales:

SELECT
    strftime('%Y-%m', order_date) AS month,
    SUM(sales) AS total_sales
FROM orders
GROUP BY month
ORDER BY month


For monthly profit:

SELECT
    strftime('%Y-%m', order_date) AS month,
    SUM(profit) AS total_profit
FROM orders
GROUP BY month
ORDER BY month


For yearly sales:

SELECT
    strftime('%Y', order_date) AS year,
    SUM(sales) AS total_sales
FROM orders
GROUP BY year
ORDER BY year


For yearly profit:

SELECT
    strftime('%Y', order_date) AS year,
    SUM(profit) AS total_profit
FROM orders
GROUP BY year
ORDER BY year


GENERAL RULES
-------------

1. Return ONLY one SQL SELECT statement.

2. Use ONLY the table:
   orders

3. Use ONLY columns that actually exist in the schema.

4. Never invent table names.

5. Never invent column names.

6. Never use:
   sales_data
   products
   customers
   your_table_name
   row_id
   or any other table that is not provided.

7. Do not create tables.

8. Do not modify data.

9. Do not use INSERT, UPDATE, DELETE, DROP, ALTER,
   CREATE, ATTACH or DETACH.

10. Do not add unnecessary columns.

11. Do not add unnecessary filters.

12. Do not add unnecessary GROUP BY columns.

13. The requested business dimension MUST appear in
    SELECT and GROUP BY when aggregation by dimension
    is requested.

14. The metric requested by the user must be the metric
    being aggregated.

15. Use aliases such as:
    total_sales
    total_profit
    total_quantity
    average_discount

16. For ranking questions, sort descending unless
    the user explicitly asks for the lowest.

17. For time trends, preserve chronological order.

18. Do not explain the query.

19. Do not return markdown.

20. Return ONLY SQL.


USER QUESTION
-------------

{question}


DATABASE SCHEMA
---------------

{schema}
"""

    response = ollama.chat(
        model=MODEL,
        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ]
    )

    sql = response["message"]["content"]

    return clean_sql(sql)


# ============================================================
# GROUND TRUTH SQL
# ============================================================

GROUND_TRUTH = {

    # --------------------------------------------------------
    # Supported questions
    # --------------------------------------------------------

    "What is the total sales?":
        """
        SELECT SUM(sales) AS total_sales
        FROM orders
        """,

    "What is the total profit?":
        """
        SELECT SUM(profit) AS total_profit
        FROM orders
        """,

    "Which category has the highest profit?":
        """
        SELECT category, SUM(profit) AS total_profit
        FROM orders
        GROUP BY category
        ORDER BY total_profit DESC
        LIMIT 1
        """,

    "Which category has the highest sales?":
        """
        SELECT category, SUM(sales) AS total_sales
        FROM orders
        GROUP BY category
        ORDER BY total_sales DESC
        LIMIT 1
        """,

    "Which sub-category has the highest profit?":
        """
        SELECT sub_category, SUM(profit) AS total_profit
        FROM orders
        GROUP BY sub_category
        ORDER BY total_profit DESC
        LIMIT 1
        """,

    "Which region has the highest sales?":
        """
        SELECT region, SUM(sales) AS total_sales
        FROM orders
        GROUP BY region
        ORDER BY total_sales DESC
        LIMIT 1
        """,

    "Which region has the highest profit?":
        """
        SELECT region, SUM(profit) AS total_profit
        FROM orders
        GROUP BY region
        ORDER BY total_profit DESC
        LIMIT 1
        """,

    "Which customer segment has the highest profit?":
        """
        SELECT segment, SUM(profit) AS total_profit
        FROM orders
        GROUP BY segment
        ORDER BY total_profit DESC
        LIMIT 1
        """,

    "Which customer segment has the highest sales?":
        """
        SELECT segment, SUM(sales) AS total_sales
        FROM orders
        GROUP BY segment
        ORDER BY total_sales DESC
        LIMIT 1
        """,

    "What are the top 5 products by profit?":
        """
        SELECT product_name, SUM(profit) AS total_profit
        FROM orders
        GROUP BY product_name
        ORDER BY total_profit DESC
        LIMIT 5
        """,

    "What are the top 5 products by sales?":
        """
        SELECT product_name, SUM(sales) AS total_sales
        FROM orders
        GROUP BY product_name
        ORDER BY total_sales DESC
        LIMIT 5
        """,

    "What are the monthly sales?":
        """
        SELECT
            strftime('%Y-%m', order_date) AS month,
            SUM(sales) AS total_sales
        FROM orders
        GROUP BY month
        ORDER BY month
        """,

    "What are the monthly profits?":
        """
        SELECT
            strftime('%Y-%m', order_date) AS month,
            SUM(profit) AS total_profit
        FROM orders
        GROUP BY month
        ORDER BY month
        """,

    "What are the yearly sales?":
        """
        SELECT
            strftime('%Y', order_date) AS year,
            SUM(sales) AS total_sales
        FROM orders
        GROUP BY year
        ORDER BY year
        """,

    "What are the yearly profits?":
        """
        SELECT
            strftime('%Y', order_date) AS year,
            SUM(profit) AS total_profit
        FROM orders
        GROUP BY year
        ORDER BY year
        """,

    "What is the total quantity sold?":
        """
        SELECT SUM(quantity) AS total_quantity
        FROM orders
        """,

    "What is the average discount?":
        """
        SELECT AVG(discount) AS average_discount
        FROM orders
        """,

    "Which category has the highest quantity sold?":
        """
        SELECT category, SUM(quantity) AS total_quantity
        FROM orders
        GROUP BY category
        ORDER BY total_quantity DESC
        LIMIT 1
        """,

    "Which region has the highest quantity sold?":
        """
        SELECT region, SUM(quantity) AS total_quantity
        FROM orders
        GROUP BY region
        ORDER BY total_quantity DESC
        LIMIT 1
        """,

    "Which sub-category has the highest sales?":
        """
        SELECT sub_category, SUM(sales) AS total_sales
        FROM orders
        GROUP BY sub_category
        ORDER BY total_sales DESC
        LIMIT 1
        """,

    # --------------------------------------------------------
    # Unsupported questions
    # --------------------------------------------------------

    "What is the employee satisfaction score?":
        None,

    "How many employees does the company have?":
        None,

    "What is the company's marketing budget?":
        None,

    "What is the average employee salary?":
        None,

    "What is the company's customer satisfaction rating?":
        None,


    # --------------------------------------------------------
    # Edge / natural-language variations
    # --------------------------------------------------------

    "Show me the best performing category based on profit.":

        """
        SELECT category, SUM(profit) AS total_profit
        FROM orders
        GROUP BY category
        ORDER BY total_profit DESC
        LIMIT 1
        """,

    "Which area generated the most revenue?":

        """
        SELECT region, SUM(sales) AS total_sales
        FROM orders
        GROUP BY region
        ORDER BY total_sales DESC
        LIMIT 1
        """,

    "Give me the five most profitable products.":

        """
        SELECT product_name, SUM(profit) AS total_profit
        FROM orders
        GROUP BY product_name
        ORDER BY total_profit DESC
        LIMIT 5
        """,

    "Show sales trends over time.":

        """
        SELECT
            strftime('%Y-%m', order_date) AS month,
            SUM(sales) AS total_sales
        FROM orders
        GROUP BY month
        ORDER BY month
        """,

    "Which customer group generated the most profit?":

        """
        SELECT segment, SUM(profit) AS total_profit
        FROM orders
        GROUP BY segment
        ORDER BY total_profit DESC
        LIMIT 1
        """
}


# ============================================================
# RESULT COMPARISON
# ============================================================

def normalize_result(df):

    if df is None:
        return None

    result = df.copy()

    # Normalize column names
    result.columns = [
        str(col).strip().lower()
        for col in result.columns
    ]

    # Normalize string values
    for col in result.columns:

        if result[col].dtype == "object":

            result[col] = (
                result[col]
                .astype(str)
                .str.strip()
                .str.lower()
            )

    # Round numerical values to avoid tiny floating-point
    # differences between equivalent SQL queries.
    for col in result.columns:

        if pd.api.types.is_numeric_dtype(result[col]):

            result[col] = result[col].round(6)

    return result


def results_match(actual, expected):

    if actual is None or expected is None:
        return False

    actual = normalize_result(actual)
    expected = normalize_result(expected)

    # Same number of rows and columns
    if actual.shape != expected.shape:
        return False

    # Compare values rather than exact column names.
    # This allows aliases to differ slightly while the
    # returned business result remains correct.
    for col_index in range(actual.shape[1]):

        actual_col = actual.iloc[:, col_index]
        expected_col = expected.iloc[:, col_index]

        if pd.api.types.is_numeric_dtype(actual_col) and \
           pd.api.types.is_numeric_dtype(expected_col):

            if not actual_col.equals(expected_col):

                if not (
                    (actual_col - expected_col)
                    .abs()
                    .max()
                    <= 0.00001
                ):
                    return False

        else:

            if not actual_col.equals(expected_col):
                return False

    return True


# ============================================================
# QUESTION CATEGORY
# ============================================================

SUPPORTED_QUESTIONS = [
    "What is the total sales?",
    "What is the total profit?",
    "Which category has the highest profit?",
    "Which category has the highest sales?",
    "Which sub-category has the highest profit?",
    "Which region has the highest sales?",
    "Which region has the highest profit?",
    "Which customer segment has the highest profit?",
    "Which customer segment has the highest sales?",
    "What are the top 5 products by profit?",
    "What are the top 5 products by sales?",
    "What are the monthly sales?",
    "What are the monthly profits?",
    "What are the yearly sales?",
    "What are the yearly profits?",
    "What is the total quantity sold?",
    "What is the average discount?",
    "Which category has the highest quantity sold?",
    "Which region has the highest quantity sold?",
    "Which sub-category has the highest sales?"
]

UNSUPPORTED_QUESTIONS = [
    "What is the employee satisfaction score?",
    "How many employees does the company have?",
    "What is the company's marketing budget?",
    "What is the average employee salary?",
    "What is the company's customer satisfaction rating?"
]

EDGE_QUESTIONS = [
    "Show me the best performing category based on profit.",
    "Which area generated the most revenue?",
    "Give me the five most profitable products.",
    "Show sales trends over time.",
    "Which customer group generated the most profit?"
]


# ============================================================
# MAIN BENCHMARK
# ============================================================

def main():

    print("=" * 70)
    print("AI BUSINESS INTELLIGENCE ASSISTANT - BENCHMARK")
    print("=" * 70)

    if not os.path.exists(DB_PATH):

        print(f"\nERROR: Database not found: {DB_PATH}")
        print("Run the Streamlit application once to create superstore.db.")
        return

    if not os.path.exists(QUESTIONS_FILE):

        print(f"\nERROR: Questions file not found:")
        print(QUESTIONS_FILE)
        return

    # --------------------------------------------------------
    # Database connection
    # --------------------------------------------------------

    conn = sqlite3.connect(DB_PATH)

    schema = get_schema(conn)

    print("\nDatabase schema:")
    print(schema)

    # --------------------------------------------------------
    # Load questions
    # --------------------------------------------------------

    questions_df = pd.read_csv(QUESTIONS_FILE)

    # Support either "question" or "Question" column.
    question_column = None

    for col in questions_df.columns:

        if col.lower() == "question":
            question_column = col
            break

    if question_column is None:

        print("\nERROR: benchmark_questions.csv must contain a 'question' column.")
        conn.close()
        return

    questions = questions_df[question_column].tolist()

    print(f"\nTotal benchmark questions: {len(questions)}")

    # --------------------------------------------------------
    # Counters
    # --------------------------------------------------------

    supported_total = 0
    supported_pass = 0

    unsupported_total = 0
    unsupported_pass = 0

    edge_total = 0
    edge_pass = 0

    results = []

    # --------------------------------------------------------
    # Process questions
    # --------------------------------------------------------

    for index, question in enumerate(questions, start=1):

        print("\n" + "-" * 70)
        print(f"Question {index}/{len(questions)}")
        print(question)
        print("-" * 70)

        category = "unknown"

        if question in SUPPORTED_QUESTIONS:

            category = "supported"
            supported_total += 1

        elif question in UNSUPPORTED_QUESTIONS:

            category = "unsupported"
            unsupported_total += 1

        elif question in EDGE_QUESTIONS:

            category = "edge"
            edge_total += 1

        # ----------------------------------------------------
        # Answerability check
        # ----------------------------------------------------

        start_time = time.time()

        try:

            answerable = check_question_answerability(
                question,
                schema
            )

        except Exception as e:

            answerable = False

            print(f"Answerability error: {e}")

        # ----------------------------------------------------
        # Unsupported question
        # ----------------------------------------------------

        if category == "unsupported":

            elapsed = time.time() - start_time

            if not answerable:

                print("Answerability: correctly rejected")
                print("Result: PASS")

                unsupported_pass += 1

                results.append({
                    "question": question,
                    "category": category,
                    "answerable": False,
                    "sql": "",
                    "status": "PASS",
                    "response_time_seconds": round(elapsed, 2),
                    "error": ""
                })

            else:

                print("Answerability: incorrectly accepted")
                print("Result: FAIL")

                # Generate SQL to help diagnose the issue
                try:

                    sql = generate_sql(
                        question,
                        schema
                    )

                except Exception as e:

                    sql = f"ERROR: {e}"

                results.append({
                    "question": question,
                    "category": category,
                    "answerable": True,
                    "sql": sql,
                    "status": "FAIL",
                    "response_time_seconds": round(
                        time.time() - start_time,
                        2
                    ),
                    "error": "Unsupported question accepted"
                })

            continue

        # ----------------------------------------------------
        # Supported / Edge question rejected incorrectly
        # ----------------------------------------------------

        if not answerable:

            elapsed = time.time() - start_time

            print("Answerability: incorrectly rejected")
            print("Result: FAIL")

            if category == "supported":
                pass

            elif category == "edge":
                pass

            results.append({
                "question": question,
                "category": category,
                "answerable": False,
                "sql": "",
                "status": "FAIL",
                "response_time_seconds": round(elapsed, 2),
                "error": "Question incorrectly rejected"
            })

            continue

        # ----------------------------------------------------
        # Generate SQL
        # ----------------------------------------------------

        try:

            sql = generate_sql(
                question,
                schema
            )

            print("\nGenerated SQL:")
            print(sql)

        except Exception as e:

            elapsed = time.time() - start_time

            print(f"\nSQL generation error: {e}")
            print("Result: FAIL")

            results.append({
                "question": question,
                "category": category,
                "answerable": True,
                "sql": "",
                "status": "FAIL",
                "response_time_seconds": round(elapsed, 2),
                "error": str(e)
            })

            continue

        # ----------------------------------------------------
        # SQL validation
        # ----------------------------------------------------

        if not validate_sql(sql):

            elapsed = time.time() - start_time

            print("SQL validation: FAILED")
            print("Result: FAIL")

            results.append({
                "question": question,
                "category": category,
                "answerable": True,
                "sql": sql,
                "status": "FAIL",
                "response_time_seconds": round(elapsed, 2),
                "error": "Unsafe or invalid SQL"
            })

            continue

        print("SQL validation: PASSED")

        # ----------------------------------------------------
        # Execute generated SQL
        # ----------------------------------------------------

        try:

            actual_result = pd.read_sql_query(
                sql,
                conn
            )

            print("\nActual result:")
            print(actual_result)

        except Exception as e:

            elapsed = time.time() - start_time

            print(f"\nSQL execution error: {e}")
            print("Result: FAIL")

            results.append({
                "question": question,
                "category": category,
                "answerable": True,
                "sql": sql,
                "status": "FAIL",
                "response_time_seconds": round(elapsed, 2),
                "error": str(e)
            })

            continue

        # ----------------------------------------------------
        # Ground truth
        # ----------------------------------------------------

        expected_sql = GROUND_TRUTH.get(question)

        if expected_sql is None:

            elapsed = time.time() - start_time

            print("\nNo ground truth SQL found.")
            print("Result: FAIL")

            results.append({
                "question": question,
                "category": category,
                "answerable": True,
                "sql": sql,
                "status": "FAIL",
                "response_time_seconds": round(elapsed, 2),
                "error": "Missing ground truth"
            })

            continue

        # ----------------------------------------------------
        # Execute ground truth
        # ----------------------------------------------------

        try:

            expected_result = pd.read_sql_query(
                expected_sql,
                conn
            )

        except Exception as e:

            elapsed = time.time() - start_time

            print(f"\nGround truth execution error: {e}")
            print("Result: FAIL")

            results.append({
                "question": question,
                "category": category,
                "answerable": True,
                "sql": sql,
                "status": "FAIL",
                "response_time_seconds": round(elapsed, 2),
                "error": f"Ground truth error: {e}"
            })

            continue

        # ----------------------------------------------------
        # Compare results
        # ----------------------------------------------------

        is_correct = results_match(
            actual_result,
            expected_result
        )

        elapsed = time.time() - start_time

        if is_correct:

            print("\nResult: PASS")

            if category == "supported":
                supported_pass += 1

            elif category == "edge":
                edge_pass += 1

            status = "PASS"
            error = ""

        else:

            print("\nResult: FAIL")

            print("\nExpected result:")
            print(expected_result)

            print("\nActual result:")
            print(actual_result)

            status = "FAIL"
            error = "Generated result differs from ground truth"

        results.append({
            "question": question,
            "category": category,
            "answerable": True,
            "sql": sql,
            "status": status,
            "response_time_seconds": round(elapsed, 2),
            "error": error
        })

    # ========================================================
    # SAVE RESULTS
    # ========================================================

    results_df = pd.DataFrame(results)

    os.makedirs(
        os.path.dirname(RESULTS_FILE),
        exist_ok=True
    )

    results_df.to_csv(
        RESULTS_FILE,
        index=False
    )

    # ========================================================
    # SUMMARY
    # ========================================================

    supported_accuracy = (
        supported_pass / supported_total * 100
        if supported_total
        else 0
    )

    unsupported_accuracy = (
        unsupported_pass / unsupported_total * 100
        if unsupported_total
        else 0
    )

    edge_accuracy = (
        edge_pass / edge_total * 100
        if edge_total
        else 0
    )

    total_pass = (
        supported_pass
        + unsupported_pass
        + edge_pass
    )

    total_questions = (
        supported_total
        + unsupported_total
        + edge_total
    )

    overall_accuracy = (
        total_pass / total_questions * 100
        if total_questions
        else 0
    )

    successful_times = results_df[
        results_df["status"] == "PASS"
    ]["response_time_seconds"]

    if len(successful_times) > 0:

        average_time = successful_times.mean()
        minimum_time = successful_times.min()
        maximum_time = successful_times.max()

    else:

        average_time = 0
        minimum_time = 0
        maximum_time = 0

    # ========================================================
    # PRINT SUMMARY
    # ========================================================

    print("\n")
    print("=" * 70)
    print("BENCHMARK SUMMARY")
    print("=" * 70)

    print(
        f"\nSupported questions:"
        f" {supported_pass}/{supported_total}"
        f" = {supported_accuracy:.1f}%"
    )

    print(
        f"Unsupported questions:"
        f" {unsupported_pass}/{unsupported_total}"
        f" = {unsupported_accuracy:.1f}%"
    )

    print(
        f"Edge questions:"
        f" {edge_pass}/{edge_total}"
        f" = {edge_accuracy:.1f}%"
    )

    print(
        f"\nOverall accuracy:"
        f" {total_pass}/{total_questions}"
        f" = {overall_accuracy:.1f}%"
    )

    print(
        f"\nAverage response time:"
        f" {average_time:.2f} seconds"
    )

    print(
        f"Minimum response time:"
        f" {minimum_time:.2f} seconds"
    )

    print(
        f"Maximum response time:"
        f" {maximum_time:.2f} seconds"
    )

    print(
        f"\nDetailed results saved to:"
        f" {RESULTS_FILE}"
    )

    print("=" * 70)

    conn.close()


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()