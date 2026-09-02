import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import sqlite3
from ollama import chat


# ============================================================
# PAGE CONFIGURATION
# ============================================================

st.set_page_config(
    page_title="AI Business Intelligence Assistant",
    page_icon="🤖",
    layout="wide"
)


# ============================================================
# CUSTOM CSS
# ============================================================

st.markdown(
    """
    <style>

    .main-title {
        font-size: 42px;
        font-weight: 700;
        margin-bottom: 5px;
    }

    .subtitle {
        font-size: 18px;
        color: #9ca3af;
        margin-bottom: 30px;
    }

    .section-title {
        font-size: 26px;
        font-weight: 600;
        margin-top: 25px;
    }

    .insight-box {
        padding: 18px;
        border-radius: 10px;
        border: 1px solid #444;
        background-color: #1f2937;
    }

    </style>
    """,
    unsafe_allow_html=True
)


# ============================================================
# TITLE
# ============================================================

st.markdown(
    '<div class="main-title">🤖 AI Business Intelligence Assistant</div>',
    unsafe_allow_html=True
)

st.markdown(
    '<div class="subtitle">'
    'Ask questions about sales data using natural language '
    'and receive SQL-driven business insights.'
    '</div>',
    unsafe_allow_html=True
)


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.header("📌 About the Project")

    st.write(
        """
        This application uses a locally hosted
        **Qwen 2.5 3B Large Language Model**
        to convert natural-language business questions
        into SQL queries.
        """
    )

    st.divider()

    st.subheader("⚙️ Technology Stack")

    st.write(
        """
        • Python  
        • Streamlit  
        • Qwen 2.5 3B  
        • Ollama  
        • SQLite  
        • Pandas  
        • Matplotlib  
        """
    )

    st.divider()

    st.subheader("🔄 How It Works")

    st.write(
        """
        **1.** User asks a business question

        **2.** Qwen generates SQL

        **3.** SQL is validated

        **4.** SQLite executes the query

        **5.** Results are displayed

        **6.** A visualization is generated

        **7.** AI provides a business insight
        """
    )

    st.divider()

    st.subheader("💬 Example Questions")

    st.write(
        """
        • Which category has the highest profit?

        • What are the top 5 products by sales?

        • Which region has the highest sales?

        • Which customer segment has the highest profit?

        • What are the monthly sales?
        """
    )


# ============================================================
# LOAD DATA
# ============================================================

@st.cache_data
def load_data():

    df = pd.read_csv(
        "data/superstore_orders.csv",
        encoding="cp1252"
    )

    # Normalize column names
    df.columns = (
        df.columns
        .str.strip()
        .str.lower()
        .str.replace("-", "_", regex=False)
        .str.replace("/", "_", regex=False)
        .str.replace(" ", "_", regex=False)
    )

    # Convert date columns
    df["order_date"] = pd.to_datetime(
        df["order_date"],
        dayfirst=True,
        errors="coerce"
    )

    df["ship_date"] = pd.to_datetime(
        df["ship_date"],
        dayfirst=True,
        errors="coerce"
    )

    return df


df = load_data()


# ============================================================
# CREATE SQLITE DATABASE
# ============================================================

conn = sqlite3.connect("superstore.db")

df.to_sql(
    "orders",
    conn,
    if_exists="replace",
    index=False
)

conn.close()


# ============================================================
# DATASET OVERVIEW
# ============================================================

st.markdown(
    '<div class="section-title">📊 Dataset Overview</div>',
    unsafe_allow_html=True
)

col1, col2, col3 = st.columns(3)

with col1:

    st.metric(
        "Total Records",
        f"{len(df):,}"
    )

with col2:

    st.metric(
        "Total Sales",
        f"${df['sales'].sum():,.2f}"
    )

with col3:

    st.metric(
        "Total Profit",
        f"${df['profit'].sum():,.2f}"
    )


# ============================================================
# SQL VALIDATION
# ============================================================

def validate_sql(sql):

    sql_upper = sql.strip().upper()

    forbidden_keywords = [
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

    # Query must start with SELECT
    if not sql_upper.startswith("SELECT"):

        return False, "Only SELECT queries are allowed."

    # Check dangerous SQL commands
    for keyword in forbidden_keywords:

        if keyword in sql_upper:

            return False, (
                f"Unsafe SQL keyword detected: {keyword}"
            )

    return True, "SQL is safe."


# ============================================================
# GET DATABASE SCHEMA
# ============================================================

def get_schema():

    conn = sqlite3.connect("superstore.db")

    cursor = conn.execute(
        "PRAGMA table_info(orders)"
    )

    columns = cursor.fetchall()

    conn.close()

    column_names = [
        column[1]
        for column in columns
    ]

    return column_names


schema_columns = get_schema()

schema = f"""
Table: orders

Columns:
{", ".join(schema_columns)}
"""


# ============================================================
# USER QUESTION
# ============================================================

st.markdown(
    '<div class="section-title">💬 Ask Your Question</div>',
    unsafe_allow_html=True
)

question = st.text_input(
    "Enter a business question:",
    placeholder="Example: Which category has the highest profit?"
)


# ============================================================
# ANALYZE BUTTON
# ============================================================

if st.button(
    "🔍 Analyze",
    use_container_width=False
):

    if not question.strip():

        st.warning(
            "Please enter a question first."
        )

    else:

        # ====================================================
        # CREATE SQL GENERATION PROMPT
        # ====================================================

        prompt = f"""
You are an expert SQLite SQL analyst.

You are given a SQLite database with the following schema:

{schema}

Your task is to convert the user's business question into
ONE valid SQLite SELECT query.

IMPORTANT:
- You MUST use only columns that appear in the schema.
- NEVER invent or rename a column.
- The database uses lowercase snake_case column names.
- "customer segment" means the column `segment`.
- "product name" means the column `product_name`.
- "sub-category" means the column `sub_category`.
- "order date" means the column `order_date`.
- "sales" means the column `sales`.
- "profit" means the column `profit`.
- "quantity" means the column `quantity`.
- "discount" means the column `discount`.
- "region" means the column `region`.
- "category" means the column `category`.

User question:
{question}

Rules:
1. Return ONLY the SQL query.
2. Do not use markdown or code fences.
3. Do not explain the query.
4. Only generate SELECT queries.
5. Use ONLY the exact table and column names provided in the schema.
6. Never create a column name based on natural-language wording.
7. For highest/lowest questions involving profit or sales,
   use SUM() when comparing categories, segments, regions,
   sub-categories or products.
8. For "top N" questions, use ORDER BY and LIMIT.
9. For monthly sales/profit trends, use:
   strftime('%Y-%m', order_date)
   and GROUP BY the resulting month.
10. For monthly trends, order the results chronologically by month.
11. For time trends, return one row per month, not one row per transaction.
12. When ranking or finding the highest/lowest value,
    return both the relevant name/category and the calculated value.
13. Use clear aliases for calculated columns.
14. Do not use columns that are not present in the schema.

Return only the SQL query.
"""


        # ====================================================
        # ASK QWEN
        # ====================================================

        with st.spinner(
            "🤖 AI is analyzing your question..."
        ):

            response = chat(
                model="qwen2.5:3b",
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ]
            )


        # ====================================================
        # CLEAN GENERATED SQL
        # ====================================================

        sql_query = response.message.content.strip()

        sql_query = (
            sql_query
            .replace("```sql", "")
            .replace("```", "")
            .strip()
        )

        # Remove unnecessary outer parentheses
        if (
            sql_query.startswith("(")
            and sql_query.endswith(")")
        ):

            sql_query = sql_query[1:-1].strip()


        # ====================================================
        # GENERATED SQL
        # ====================================================

        with st.expander(
            "🧠 View Generated SQL",
            expanded=False
        ):

            st.code(
                sql_query,
                language="sql"
            )


        # ====================================================
        # SQL VALIDATION
        # ====================================================

        is_safe, validation_message = validate_sql(
            sql_query
        )


        if not is_safe:

            st.error(
                f"SQL query rejected: {validation_message}"
            )

        else:

            # =================================================
            # DATABASE CONNECTION
            # =================================================

            conn = sqlite3.connect(
                "superstore.db"
            )

            try:

                # =============================================
                # EXECUTE SQL
                # =============================================

                result = pd.read_sql_query(
                    sql_query,
                    conn
                )


                # =============================================
                # QUERY RESULT
                # =============================================

                st.subheader("📊 Query Result")

                st.dataframe(
                    result,
                    use_container_width=True
                )


                # =============================================
                # AUTOMATIC VISUALIZATION
                # =============================================

                if (
                    len(result.columns) >= 2
                    and len(result) > 1
                ):

                    st.subheader("📈 Visualization")

                    try:

                        x_column = result.columns[0]
                        y_column = result.columns[1]

                        if pd.api.types.is_numeric_dtype(
                            result[y_column]
                        ):

                            fig, ax = plt.subplots(
                                figsize=(12, 6)
                            )

                            x_values = (
                                result[x_column]
                                .astype(str)
                            )

                            y_values = result[y_column]


                            # ---------------------------------
                            # Detect time-based results
                            # ---------------------------------

                            is_time_series = (
                                "month"
                                in x_column.lower()
                                or "date"
                                in x_column.lower()
                                or "year"
                                in x_column.lower()
                            )


                            # ---------------------------------
                            # TIME SERIES
                            # ---------------------------------

                            if is_time_series:

                                ax.plot(
                                    x_values,
                                    y_values,
                                    marker="o"
                                )

                                ax.set_xlabel(
                                    x_column
                                )

                                ax.set_ylabel(
                                    y_column
                                )

                                total_points = len(
                                    x_values
                                )

                                # Display approximately
                                # eight x-axis labels
                                if total_points > 8:

                                    tick_step = max(
                                        1,
                                        total_points // 8
                                    )

                                    tick_positions = list(
                                        range(
                                            0,
                                            total_points,
                                            tick_step
                                        )
                                    )

                                    # Include final point
                                    if (
                                        tick_positions[-1]
                                        != total_points - 1
                                    ):

                                        tick_positions.append(
                                            total_points - 1
                                        )

                                else:

                                    tick_positions = list(
                                        range(
                                            total_points
                                        )
                                    )


                                ax.set_xticks(
                                    tick_positions
                                )

                                ax.set_xticklabels(
                                    [
                                        x_values.iloc[i]
                                        for i in tick_positions
                                    ],
                                    rotation=0,
                                    ha="center"
                                )

                                ax.grid(
                                    axis="y",
                                    alpha=0.3
                                )


                            # ---------------------------------
                            # CATEGORY BAR CHART
                            # ---------------------------------

                            else:

                                ax.bar(
                                    x_values,
                                    y_values
                                )

                                ax.set_xlabel(
                                    x_column
                                )

                                ax.set_ylabel(
                                    y_column
                                )

                                plt.xticks(
                                    rotation=45,
                                    ha="right"
                                )


                            plt.tight_layout()

                            st.pyplot(fig)

                            plt.close(fig)


                    except Exception as e:

                        st.warning(
                            "Visualization could not be "
                            f"generated: {e}"
                        )


                # =============================================
                # GENERATE AI BUSINESS INSIGHT
                # =============================================

                result_text = result.to_string(
                    index=False
                )

                insight_prompt = f"""
You are a business analyst.

The user asked:

{question}

The SQL query returned this result:

{result_text}

Give a short business insight based ONLY on
the provided result.

Do not invent information.

Keep the explanation to 2-3 sentences.
"""


                insight_response = chat(
                    model="qwen2.5:3b",
                    messages=[
                        {
                            "role": "user",
                            "content": insight_prompt
                        }
                    ]
                )


                insight = (
                    insight_response
                    .message
                    .content
                    .strip()
                )


                # =============================================
                # BUSINESS INSIGHT
                # =============================================

                st.subheader(
                    "💡 AI Business Insight"
                )

                st.info(insight)


            except Exception as e:

                st.error(
                    f"SQL execution error: {e}"
                )

            finally:

                conn.close()