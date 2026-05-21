-- Canonical TPC-H 8-table schema (region, nation, supplier, customer, part,
-- partsupp, orders, lineitem) with SF-tiny synthetic data tuned to the
-- TPC-H spec's validation-section parameter values for Q2/Q4/Q7-Q22.
--
-- Compatible with the existing tpch_q{1,3,5,6,10} fixtures' query shapes
-- (region/nation/customer/orders/lineitem share the same surface), but
-- adds the part/supplier/partsupp tables and the columns the new queries
-- need (l_partkey, l_suppkey, o_orderstatus, c_phone, c_acctbal, etc.).
-- Each fixture has its own setup.sql; the existing 5 fixtures are unchanged.

CREATE OR REPLACE TABLE `${DATASET}.region` (
  r_regionkey INT64, r_name STRING, r_comment STRING
);
INSERT INTO `${DATASET}.region` VALUES
  (0, "AFRICA",      "lar deposits"),
  (1, "AMERICA",     "hs use ironic"),
  (2, "ASIA",        "thinly even ridges"),
  (3, "EUROPE",      "ly final courts"),
  (4, "MIDDLE EAST", "uickly special");

CREATE OR REPLACE TABLE `${DATASET}.nation` (
  n_nationkey INT64, n_name STRING, n_regionkey INT64, n_comment STRING
);
INSERT INTO `${DATASET}.nation` VALUES
  ( 0, "ALGERIA",        0, "haggle quickly"),
  ( 1, "ARGENTINA",      1, "al foxes"),
  ( 2, "BRAZIL",         1, "y alongside"),
  ( 3, "CANADA",         1, "eas hang ironic"),
  ( 4, "EGYPT",          4, "y above the carefully"),
  ( 5, "ETHIOPIA",       0, "ven packages wake"),
  ( 6, "FRANCE",         3, "refully final reque"),
  ( 7, "GERMANY",        3, "l platelets"),
  ( 8, "INDIA",          2, "ss excuses cajole"),
  ( 9, "INDONESIA",      2, "slyly express"),
  (10, "IRAN",           4, "efully alongside of"),
  (11, "IRAQ",           4, "nic deposits"),
  (12, "JAPAN",          2, "ously. final"),
  (13, "JORDAN",         4, "ic deposits are blith"),
  (14, "KENYA",          0, "pending excuses"),
  (15, "MOROCCO",        0, "rns. blithely bold"),
  (16, "MOZAMBIQUE",     0, "s. ironic, unusual"),
  (17, "PERU",           1, "platelets. blithely"),
  (18, "CHINA",          2, "c dependencies"),
  (19, "ROMANIA",        3, "ular asymptotes"),
  (20, "SAUDI ARABIA",   4, "ts. silent requests"),
  (21, "VIETNAM",        2, "hely enticingly"),
  (22, "RUSSIA",         3, "requests against"),
  (23, "UNITED KINGDOM", 3, "eans boost carefully"),
  (24, "UNITED STATES",  1, "y final packages");

CREATE OR REPLACE TABLE `${DATASET}.supplier` (
  s_suppkey INT64, s_name STRING, s_address STRING, s_nationkey INT64,
  s_phone STRING, s_acctbal NUMERIC, s_comment STRING
);
INSERT INTO `${DATASET}.supplier` VALUES
  ( 1, "Supplier#000000001", "47 Sycamore Lane",  6, "16-101-010-0001", NUMERIC "5755.94", "each slyly above the careful"),
  ( 2, "Supplier#000000002", "89 Birch Road",     7, "17-202-020-0002", NUMERIC "4032.68", "furiously regular ideas"),
  ( 3, "Supplier#000000003", "23 Maple Avenue",   2, "12-303-030-0003", NUMERIC "4192.40", "bold accounts use slyly"),
  ( 4, "Supplier#000000004", "5 Oak Street",      3, "13-404-040-0004", NUMERIC "4641.08", "final ideas wake slyly"),
  ( 5, "Supplier#000000005", "12 Pine Drive",    20, "30-505-050-0005", NUMERIC "-283.84", "unusual requests cajole"),
  ( 6, "Supplier#000000006", "67 Elm Court",     12, "22-606-060-0006", NUMERIC "1365.79", "closely thin packages"),
  ( 7, "Supplier#000000007", "34 Cedar Lane",    23, "33-707-070-0007", NUMERIC "6820.35", "pending pinto beans"),
  ( 8, "Supplier#000000008", "56 Walnut Way",    19, "29-808-080-0008", NUMERIC "7787.46", "Customer Complaintss"),
  ( 9, "Supplier#000000009", "78 Spruce Blvd",    5, "15-909-090-0009", NUMERIC "5302.37", "ronic dolphins"),
  (10, "Supplier#000000010", "90 Aspen Court",   11, "21-010-010-0010", NUMERIC "3306.32", "finally express requests");

CREATE OR REPLACE TABLE `${DATASET}.customer` (
  c_custkey INT64, c_name STRING, c_address STRING, c_nationkey INT64,
  c_phone STRING, c_acctbal NUMERIC, c_mktsegment STRING, c_comment STRING
);
INSERT INTO `${DATASET}.customer` VALUES
  ( 1, "Customer#000000001", "123 Main St",     24, "34-111-111-1111", NUMERIC "1234.56", "BUILDING",   "requests sleep furiously"),
  ( 2, "Customer#000000002", "456 Elm St",      24, "34-222-222-2222", NUMERIC  "789.12", "AUTOMOBILE", "special requests"),
  ( 3, "Customer#000000003", "789 Oak Ave",      7, "17-333-333-3333", NUMERIC "5500.00", "MACHINERY",  "busy accounts"),
  ( 4, "Customer#000000004", "12 Pine Rd",      12, "22-444-444-4444", NUMERIC "6543.21", "BUILDING",   "final deposits"),
  ( 5, "Customer#000000005", "34 Birch Blvd",    8, "18-555-555-5555", NUMERIC "7894.65", "FURNITURE",  "thinly bold accounts"),
  ( 6, "Customer#000000006", "56 Cedar Way",     4, "14-666-666-6666", NUMERIC "2345.67", "BUILDING",   "foxes nag bravely"),
  ( 7, "Customer#000000007", "78 Maple Ct",      6, "16-777-777-7777", NUMERIC "9012.34", "BUILDING",   "special requests for items"),
  ( 8, "Customer#000000008", "90 Walnut Ave",    2, "12-888-888-8888", NUMERIC "3456.78", "AUTOMOBILE", "bold accounts sleep"),
  ( 9, "Customer#000000009", "123 Spruce Dr",   13, "23-999-999-9999", NUMERIC "8765.43", "MACHINERY",  "foxes wake bravely"),
  (10, "Customer#000000010", "345 Aspen Ln",    19, "29-101-010-1010", NUMERIC "4321.98", "HOUSEHOLD",  "final foxes"),
  (11, "Customer#000000011", "567 Birch Ave",   20, "30-202-020-2020", NUMERIC "7654.32", "BUILDING",   "requests sleep"),
  (12, "Customer#000000012", "789 Cedar Blvd",  19, "29-303-030-3030", NUMERIC "2109.87", "MACHINERY",  "special requests cajole"),
  (13, "Customer#000000013", "12 Pine St",       3, "13-404-040-4040", NUMERIC "5432.10", "BUILDING",   "bold ideas"),
  (14, "Customer#000000014", "34 Oak Way",       6, "16-505-050-5050", NUMERIC "3210.98", "BUILDING",   "regular foxes"),
  (15, "Customer#000000015", "56 Maple Dr",      7, "17-606-060-6060", NUMERIC "6543.21", "FURNITURE",  "special requests pending"),
  (16, "Customer#000000016", "78 Walnut Ct",     8, "18-707-070-7070", NUMERIC "4567.89", "BUILDING",   "idle requests sleep"),
  (17, "Customer#000000017", "90 Cedar Ave",    19, "29-808-080-8080", NUMERIC "5678.90", "FURNITURE",  "no orders here"),
  (18, "Customer#000000018", "12 Pine Blvd",    13, "23-909-090-9090", NUMERIC "8901.23", "MACHINERY",  "forming requests"),
  (19, "Customer#000000019", "34 Birch Way",    20, "30-010-101-0101", NUMERIC "6789.01", "BUILDING",   "special accounts"),
  (20, "Customer#000000020", "56 Maple Ave",     3, "13-121-212-1212", NUMERIC "7890.12", "AUTOMOBILE", "cajole carefully");

CREATE OR REPLACE TABLE `${DATASET}.part` (
  p_partkey INT64, p_name STRING, p_mfgr STRING, p_brand STRING,
  p_type STRING, p_size INT64, p_container STRING,
  p_retailprice NUMERIC, p_comment STRING
);
INSERT INTO `${DATASET}.part` VALUES
  ( 1, "goldenrod lavender brass spring rose", "Manufacturer#1", "Brand#12", "STANDARD ANODIZED BRASS",   15, "SM CASE",   NUMERIC  "901.00", "ly final dependencies"),
  ( 2, "green forest blush mint olive",        "Manufacturer#2", "Brand#23", "STANDARD POLISHED BRASS",   15, "MED BOX",   NUMERIC  "903.00", "final dependencies"),
  ( 3, "forest sky blue red yellow",           "Manufacturer#3", "Brand#23", "ECONOMY POLISHED STEEL",    14, "MED BOX",   NUMERIC  "905.00", "unusual accounts"),
  ( 4, "orange brown gold silver red",         "Manufacturer#1", "Brand#12", "SMALL PLATED COPPER",        1, "SM PACK",   NUMERIC  "900.00", "requests sleep"),
  ( 5, "green emerald jade mint lime",         "Manufacturer#2", "Brand#34", "LARGE BRUSHED TIN",         23, "LG CASE",   NUMERIC  "910.00", "bold accounts"),
  ( 6, "PROMO blush red pink rose",            "Manufacturer#3", "Brand#45", "PROMO POLISHED COPPER",     45, "JUMBO BAG", NUMERIC  "906.00", "special requests"),
  ( 7, "beige tan ivory cream taupe",          "Manufacturer#1", "Brand#11", "SMALL POLISHED NICKEL",     36, "SM BOX",    NUMERIC  "900.00", "final foxes"),
  ( 8, "maroon plum lavender lilac magenta",   "Manufacturer#4", "Brand#42", "ECONOMY ANODIZED STEEL",    19, "MED BAG",   NUMERIC  "917.91", "unusual ideas"),
  ( 9, "green olive sage mint chartreuse",     "Manufacturer#2", "Brand#34", "MEDIUM POLISHED COPPER",    14, "WRAP DRUM", NUMERIC  "908.00", "silent foxes"),
  (10, "gold silver bronze platinum pewter",   "Manufacturer#3", "Brand#23", "SMALL POLISHED BRASS",      15, "JUMBO CAN", NUMERIC  "904.00", "careful packages"),
  (11, "PROMO turquoise teal cyan blue",       "Manufacturer#4", "Brand#34", "PROMO ANODIZED BRASS",       9, "LG BOX",    NUMERIC  "914.00", "final accounts"),
  (12, "forest pine cedar oak birch",          "Manufacturer#1", "Brand#11", "STANDARD BRUSHED COPPER",   49, "WRAP BOX",  NUMERIC  "916.00", "fluffily fluffy"),
  (13, "crimson scarlet ruby carmine rose",    "Manufacturer#2", "Brand#21", "LARGE POLISHED BRASS",      15, "MED CASE",  NUMERIC  "905.00", "careful pinto beans"),
  (14, "aqua marine ocean cyan azure",         "Manufacturer#5", "Brand#52", "PROMO BURNISHED STEEL",      5, "SM PKG",    NUMERIC  "912.00", "special excuses"),
  (15, "moss olive verdant sage chartreuse",   "Manufacturer#4", "Brand#42", "ECONOMY POLISHED COPPER",   36, "WRAP JAR",  NUMERIC  "923.00", "special foxes"),
  (16, "forest sage moss spruce fern",         "Manufacturer#3", "Brand#33", "STANDARD BURNISHED NICKEL", 19, "JUMBO PKG", NUMERIC  "903.00", "pending requests"),
  (17, "gold silver platinum ivory pewter",    "Manufacturer#5", "Brand#55", "MEDIUM POLISHED BRASS",     14, "SM BAG",    NUMERIC  "930.00", "busy requests"),
  (18, "taupe khaki beige cream sand",         "Manufacturer#1", "Brand#13", "LARGE POLISHED COPPER",      3, "WRAP DRUM", NUMERIC  "900.00", "final accounts");

CREATE OR REPLACE TABLE `${DATASET}.partsupp` (
  ps_partkey INT64, ps_suppkey INT64, ps_availqty INT64,
  ps_supplycost NUMERIC, ps_comment STRING
);
INSERT INTO `${DATASET}.partsupp` VALUES
  ( 1, 1, 3325, NUMERIC "772.43", "pending platelets"),
  ( 1, 2, 8076, NUMERIC "593.16", "closely careful foxes"),
  ( 1, 7, 5050, NUMERIC "824.50", "idle pinto"),
  ( 2, 2, 4072, NUMERIC "923.16", "slyly final"),
  ( 2, 1, 2050, NUMERIC "650.00", "fluffy excuses"),
  ( 3, 3, 6000, NUMERIC "700.00", "careful theodolites"),
  ( 3, 4, 2500, NUMERIC "850.00", "special pinto"),
  ( 4, 1, 2000, NUMERIC "400.00", "pending dolphins"),
  ( 4, 4, 3000, NUMERIC "450.00", "careful ideas"),
  ( 5, 4, 4500, NUMERIC "800.00", "fluffy requests"),
  ( 5, 9, 1500, NUMERIC "750.00", "busy accounts"),
  ( 6, 5, 8000, NUMERIC "850.00", "unusual foxes"),
  ( 7, 6, 3500, NUMERIC "720.00", "final ideas"),
  ( 8, 3, 5500, NUMERIC "780.50", "idle pinto beans"),
  ( 8, 7, 1000, NUMERIC "900.00", "special requests"),
  ( 9, 4, 2500, NUMERIC "850.00", "final foxes"),
  (10, 1, 3000, NUMERIC "600.00", "final accounts"),
  (10, 2, 4500, NUMERIC "550.00", "silent ideas"),
  (11, 2, 7000, NUMERIC "820.00", "final dolphins"),
  (12, 7, 2200, NUMERIC "900.00", "special requests"),
  (13, 1, 3500, NUMERIC "750.00", "idle final foxes"),
  (13, 9, 4000, NUMERIC "780.00", "pending requests"),
  (14, 3, 1800, NUMERIC "850.00", "careful ideas"),
  (15, 8, 4000, NUMERIC "900.00", "busy foxes"),
  (16, 1, 2500, NUMERIC "710.00", "silent accounts"),
  (17, 6, 3500, NUMERIC "880.00", "final ideas"),
  (18, 4, 1500, NUMERIC "650.00", "unusual ideas");

CREATE OR REPLACE TABLE `${DATASET}.orders` (
  o_orderkey INT64, o_custkey INT64, o_orderstatus STRING,
  o_totalprice NUMERIC, o_orderdate DATE, o_orderpriority STRING,
  o_clerk STRING, o_shippriority INT64, o_comment STRING
);
INSERT INTO `${DATASET}.orders` VALUES
  ( 1,  1, "O", NUMERIC "15000.00", DATE "1993-07-15", "1-URGENT",         "Clerk#0001", 0, "special requests"),
  ( 2,  2, "F", NUMERIC "12000.00", DATE "1993-08-20", "3-MEDIUM",         "Clerk#0002", 0, "final foxes"),
  ( 3,  3, "F", NUMERIC  "8000.00", DATE "1993-09-05", "2-HIGH",           "Clerk#0003", 0, "special pending"),
  ( 4,  4, "O", NUMERIC "20000.00", DATE "1993-09-25", "5-LOW",            "Clerk#0004", 0, "unusual accounts"),
  ( 5,  5, "F", NUMERIC "10000.00", DATE "1993-10-15", "4-NOT SPECIFIED",  "Clerk#0005", 0, "busy foxes"),
  ( 6,  6, "O", NUMERIC "18000.00", DATE "1994-01-10", "1-URGENT",         "Clerk#0006", 0, "careful dependencies"),
  ( 7,  7, "F", NUMERIC "22000.00", DATE "1994-03-20", "2-HIGH",           "Clerk#0007", 0, "final ideas"),
  ( 8,  8, "F", NUMERIC "14000.00", DATE "1994-06-15", "3-MEDIUM",         "Clerk#0008", 0, "busy accounts"),
  ( 9,  9, "O", NUMERIC "16000.00", DATE "1994-09-01", "1-URGENT",         "Clerk#0009", 0, "pending requests"),
  (10,  1, "F", NUMERIC "11000.00", DATE "1995-02-10", "1-URGENT",         "Clerk#0010", 0, "special requests"),
  (11,  2, "O", NUMERIC "17000.00", DATE "1995-05-12", "3-MEDIUM",         "Clerk#0011", 0, "final foxes"),
  (12,  7, "F", NUMERIC "13000.00", DATE "1995-08-22", "2-HIGH",           "Clerk#0012", 0, "special pending"),
  (13,  8, "O", NUMERIC "19000.00", DATE "1995-09-30", "4-NOT SPECIFIED",  "Clerk#0013", 0, "busy foxes"),
  (14,  4, "F", NUMERIC "40000.00", DATE "1995-09-25", "1-URGENT",         "Clerk#0014", 0, "large order"),
  (15, 10, "O", NUMERIC "21000.00", DATE "1996-01-05", "2-HIGH",           "Clerk#0015", 0, "careful"),
  (16, 11, "F", NUMERIC "15500.00", DATE "1996-02-10", "3-MEDIUM",         "Clerk#0016", 0, "final"),
  (17, 12, "O", NUMERIC "18500.00", DATE "1996-03-15", "1-URGENT",         "Clerk#0017", 0, "special"),
  (18, 13, "F", NUMERIC "12500.00", DATE "1995-04-18", "3-MEDIUM",         "Clerk#0018", 0, "pending"),
  (19, 14, "O", NUMERIC "13500.00", DATE "1995-06-08", "2-HIGH",           "Clerk#0019", 0, "final"),
  (20, 15, "F", NUMERIC "11500.00", DATE "1995-07-22", "4-NOT SPECIFIED",  "Clerk#0020", 0, "busy"),
  (21, 16, "F", NUMERIC "12700.00", DATE "1995-11-30", "1-URGENT",         "Clerk#0021", 0, "cajole"),
  (22,  7, "O", NUMERIC "99999.00", DATE "1995-12-15", "1-URGENT",         "Clerk#0022", 0, "big order"),
  (23,  5, "F", NUMERIC  "8700.00", DATE "1994-04-04", "3-MEDIUM",         "Clerk#0023", 0, "final"),
  (24,  1, "O", NUMERIC  "5000.00", DATE "1994-05-20", "3-MEDIUM",         "Clerk#0024", 0, "forest pickup");

CREATE OR REPLACE TABLE `${DATASET}.lineitem` (
  l_orderkey INT64, l_partkey INT64, l_suppkey INT64, l_linenumber INT64,
  l_quantity NUMERIC, l_extendedprice NUMERIC, l_discount NUMERIC, l_tax NUMERIC,
  l_returnflag STRING, l_linestatus STRING,
  l_shipdate DATE, l_commitdate DATE, l_receiptdate DATE,
  l_shipinstruct STRING, l_shipmode STRING, l_comment STRING
);
INSERT INTO `${DATASET}.lineitem` VALUES
  ( 1,  1, 1, 1, NUMERIC  "20", NUMERIC "18020.00", NUMERIC "0.04", NUMERIC "0.02", "N", "O", DATE "1993-07-20", DATE "1993-07-30", DATE "1993-08-05", "DELIVER IN PERSON", "MAIL",  "pending"),
  ( 1,  5, 4, 2, NUMERIC  "15", NUMERIC "13500.00", NUMERIC "0.02", NUMERIC "0.03", "N", "O", DATE "1993-08-01", DATE "1993-08-05", DATE "1993-08-10", "NONE",             "TRUCK", "final"),
  ( 2,  8, 3, 1, NUMERIC  "25", NUMERIC "22500.00", NUMERIC "0.05", NUMERIC "0.03", "R", "F", DATE "1993-09-01", DATE "1993-09-10", DATE "1993-09-12", "NONE",             "SHIP",  "careful"),
  ( 3,  4, 1, 1, NUMERIC  "10", NUMERIC  "4500.00", NUMERIC "0.03", NUMERIC "0.05", "R", "F", DATE "1993-09-10", DATE "1993-09-30", DATE "1993-10-05", "TAKE BACK RETURN", "AIR",   "special"),
  ( 4,  2, 2, 1, NUMERIC  "30", NUMERIC "27000.00", NUMERIC "0.04", NUMERIC "0.06", "N", "O", DATE "1993-10-01", DATE "1993-10-15", DATE "1993-10-20", "COLLECT COD",      "RAIL",  "busy"),
  ( 5,  6, 5, 1, NUMERIC  "12", NUMERIC "10872.00", NUMERIC "0.02", NUMERIC "0.04", "A", "F", DATE "1993-10-20", DATE "1993-11-05", DATE "1993-11-08", "DELIVER IN PERSON","TRUCK", "pending"),
  ( 6,  3, 3, 1, NUMERIC  "18", NUMERIC "16290.00", NUMERIC "0.05", NUMERIC "0.05", "N", "O", DATE "1994-01-15", DATE "1994-01-30", DATE "1994-02-05", "DELIVER IN PERSON","AIR",   "final"),
  ( 7,  7, 6, 1, NUMERIC  "15", NUMERIC "13500.00", NUMERIC "0.04", NUMERIC "0.03", "R", "F", DATE "1994-04-01", DATE "1994-04-05", DATE "1994-04-08", "NONE",             "SHIP",  "busy"),
  ( 8,  8, 3, 1, NUMERIC  "22", NUMERIC "20196.00", NUMERIC "0.03", NUMERIC "0.05", "N", "O", DATE "1994-07-01", DATE "1994-07-05", DATE "1994-07-08", "NONE",             "RAIL",  "careful"),
  ( 9,  9, 4, 1, NUMERIC  "14", NUMERIC "12712.00", NUMERIC "0.04", NUMERIC "0.04", "N", "O", DATE "1994-09-10", DATE "1994-09-15", DATE "1994-09-18", "TAKE BACK RETURN", "TRUCK", "pending"),
  (10,  1, 1, 1, NUMERIC   "5", NUMERIC  "4505.00", NUMERIC "0.01", NUMERIC "0.05", "N", "O", DATE "1995-04-20", DATE "1995-04-25", DATE "1995-04-28", "NONE",             "SHIP",  "special"),
  (11, 10, 1, 1, NUMERIC  "20", NUMERIC "18080.00", NUMERIC "0.05", NUMERIC "0.04", "R", "F", DATE "1995-06-01", DATE "1995-06-05", DATE "1995-06-08", "DELIVER IN PERSON","AIR",   "final"),
  (12,  2, 1, 1, NUMERIC  "12", NUMERIC "10836.00", NUMERIC "0.03", NUMERIC "0.05", "N", "O", DATE "1995-08-25", DATE "1995-08-30", DATE "1995-09-02", "NONE",             "TRUCK", "pending"),
  (13,  8, 3, 1, NUMERIC  "16", NUMERIC "14687.00", NUMERIC "0.04", NUMERIC "0.06", "N", "O", DATE "1995-10-01", DATE "1995-10-05", DATE "1995-10-08", "NONE",             "SHIP",  "careful"),
  (14,  6, 5, 1, NUMERIC  "50", NUMERIC "45300.00", NUMERIC "0.02", NUMERIC "0.03", "N", "O", DATE "1995-09-15", DATE "1995-09-20", DATE "1995-09-22", "DELIVER IN PERSON","AIR",   "PROMO"),
  (14,  1, 1, 2, NUMERIC  "30", NUMERIC "27030.00", NUMERIC "0.03", NUMERIC "0.04", "N", "O", DATE "1995-09-20", DATE "1995-09-25", DATE "1995-09-28", "NONE",             "TRUCK", "non-promo"),
  (15,  1, 7, 1, NUMERIC  "40", NUMERIC "36040.00", NUMERIC "0.04", NUMERIC "0.05", "N", "O", DATE "1996-01-20", DATE "1996-01-25", DATE "1996-01-28", "NONE",             "AIR",   "q15-target"),
  (15,  2, 7, 2, NUMERIC  "20", NUMERIC "18060.00", NUMERIC "0.02", NUMERIC "0.05", "N", "O", DATE "1996-02-15", DATE "1996-02-20", DATE "1996-02-22", "NONE",             "TRUCK", "q15-target2"),
  (16,  5, 4, 1, NUMERIC  "25", NUMERIC "23000.00", NUMERIC "0.03", NUMERIC "0.04", "N", "O", DATE "1996-02-20", DATE "1996-02-25", DATE "1996-02-28", "NONE",             "TRUCK", "q15-other"),
  (17,  3, 4, 1, NUMERIC  "18", NUMERIC "16290.00", NUMERIC "0.04", NUMERIC "0.04", "N", "O", DATE "1996-03-20", DATE "1996-03-25", DATE "1996-03-28", "NONE",             "SHIP",  "q15-other2"),
  (18,  2, 2, 1, NUMERIC   "8", NUMERIC  "7224.00", NUMERIC "0.03", NUMERIC "0.04", "N", "O", DATE "1995-05-01", DATE "1995-05-05", DATE "1995-05-08", "DELIVER IN PERSON","MAIL",  "low qty q17"),
  (19,  4, 1, 1, NUMERIC   "5", NUMERIC  "4500.00", NUMERIC "0.02", NUMERIC "0.03", "N", "O", DATE "1995-06-15", DATE "1995-06-20", DATE "1995-06-22", "DELIVER IN PERSON","AIR",   "q19-brand12"),
  (20,  6, 5, 1, NUMERIC  "10", NUMERIC  "9060.00", NUMERIC "0.02", NUMERIC "0.04", "R", "F", DATE "1995-08-01", DATE "1995-08-05", DATE "1995-08-15", "NONE",             "SHIP",  "late"),
  (20,  7, 6, 2, NUMERIC  "12", NUMERIC "10800.00", NUMERIC "0.03", NUMERIC "0.05", "N", "F", DATE "1995-08-02", DATE "1995-08-08", DATE "1995-08-10", "NONE",             "TRUCK", "on-time"),
  (21,  1, 2, 1, NUMERIC  "15", NUMERIC "13515.00", NUMERIC "0.03", NUMERIC "0.04", "N", "O", DATE "1995-12-05", DATE "1995-12-10", DATE "1995-12-12", "NONE",             "TRUCK", "q15-other3"),
  (22,  5, 4, 1, NUMERIC "100", NUMERIC "91000.00", NUMERIC "0.04", NUMERIC "0.05", "N", "O", DATE "1996-01-10", DATE "1996-01-20", DATE "1996-01-25", "NONE",             "TRUCK", "huge-1"),
  (22,  3, 3, 2, NUMERIC "150", NUMERIC "105000.00",NUMERIC "0.05", NUMERIC "0.04", "N", "O", DATE "1996-01-15", DATE "1996-01-25", DATE "1996-01-30", "NONE",             "SHIP",  "huge-2"),
  (22,  9, 4, 3, NUMERIC  "80", NUMERIC "72640.00", NUMERIC "0.03", NUMERIC "0.06", "N", "O", DATE "1996-01-20", DATE "1996-02-01", DATE "1996-02-05", "NONE",             "AIR",   "huge-3"),
  (23, 12, 7, 1, NUMERIC   "8", NUMERIC  "7328.00", NUMERIC "0.01", NUMERIC "0.04", "A", "F", DATE "1994-04-15", DATE "1994-04-20", DATE "1994-04-22", "NONE",             "MAIL",  "q9-target"),
  (24, 12, 4, 1, NUMERIC  "50", NUMERIC "45800.00", NUMERIC "0.04", NUMERIC "0.04", "N", "O", DATE "1994-06-01", DATE "1994-06-05", DATE "1994-06-08", "NONE",             "TRUCK", "q20-fwd");
