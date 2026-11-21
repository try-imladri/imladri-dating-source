"""Representative SQL research queries for the migration proof, not app cutover.

Numeric cohorts use mirrored CURRENT profiles, matching the existing Mongo
age/height report. Categorical cohorts use latest explicit history observations;
they cannot invent observations for values that were never journaled. These
queries run in a dedicated operator worker, not on the normal request path.
"""


UNITS = ("member", "couple", "couple_inviter", "couple_invitee")
ELIGIBLE = """
SELECT * FROM imladri_shadow.participants
WHERE namespace=%s AND research_version='all-answers-v1' AND role='member'
  AND NOT closing AND adult_eligible
"""
PAIRS = """
SELECT c.pair_id,a.user_id AS inviter,b.user_id AS invitee
FROM imladri_shadow.couples c
JOIN eligible a ON a.user_id=c.inviter AND a.namespace=c.namespace
JOIN eligible b ON b.user_id=c.invitee AND b.namespace=c.namespace
WHERE c.namespace=%s AND c.state='active' AND a.couples_adult_attested AND b.couples_adult_attested
  AND a.environment=b.environment AND c.environment=a.environment
"""


def unit_check(unit):
    if unit not in UNITS:
        raise ValueError("Choose a member or defined partner orientation.")


def numeric_correlation(shadow, namespace, *, unit="member", minimum=50, minimum_cell=10, round_to=5):
    """Age versus height, with the existing one-total-vote-per-couple rule."""
    unit_check(unit)
    if any(type(n) is not int or n < 1 for n in (minimum, minimum_cell, round_to)):
        raise ValueError("Expected positive disclosure limits.")
    if unit == "member":
        observations = "SELECT p.user_id AS unit_id,p.age::double precision AS x,p.height_cm::double precision AS y FROM eligible p WHERE age_is_numeric AND height_is_numeric AND age IS NOT NULL AND height_cm IS NOT NULL"
        params = [namespace]
        pair_cte = ""
    else:
        directions = []
        if unit != "couple_invitee":
            directions.append("SELECT c.pair_id AS unit_id,a.age::double precision AS x,b.height_cm::double precision AS y FROM pairs c JOIN eligible a ON a.user_id=c.inviter JOIN eligible b ON b.user_id=c.invitee WHERE a.age_is_numeric AND b.height_is_numeric AND a.age IS NOT NULL AND b.height_cm IS NOT NULL")
        if unit != "couple_inviter":
            directions.append("SELECT c.pair_id AS unit_id,b.age::double precision AS x,a.height_cm::double precision AS y FROM pairs c JOIN eligible a ON a.user_id=c.inviter JOIN eligible b ON b.user_id=c.invitee WHERE b.age_is_numeric AND a.height_is_numeric AND b.age IS NOT NULL AND a.height_cm IS NOT NULL")
        observations = " UNION ALL ".join(directions)
        params = [namespace, namespace]
        pair_cte = ",pairs AS (" + PAIRS + ")"
    sql = "WITH eligible AS (" + ELIGIBLE + ")" + pair_cte + ",observations AS (" + observations + """),
weighted AS (
 SELECT *,1.0/count(*) OVER(PARTITION BY unit_id) AS w,
 CASE WHEN x<25 THEN 0 WHEN x<35 THEN 1 WHEN x<45 THEN 2 WHEN x<55 THEN 3 WHEN x<65 THEN 4 ELSE 5 END AS xb,
 CASE WHEN y<150 THEN 0 WHEN y<160 THEN 1 WHEN y<170 THEN 2 WHEN y<180 THEN 3 WHEN y<190 THEN 4 WHEN y<200 THEN 5 ELSE 6 END AS yb
 FROM observations
),cells AS (SELECT xb,yb,sum(w) AS n FROM weighted GROUP BY xb,yb),
totals AS (SELECT sum(w) AS n,sum(w*x) AS sx,sum(w*y) AS sy,sum(w*x*x) AS sxx,
                 sum(w*y*y) AS syy,sum(w*x*y) AS sxy FROM weighted),
stats AS (SELECT *,n*sxx-sx*sx AS vx,n*syy-sy*sy AS vy,n*sxy-sx*sy AS cov FROM totals)
SELECT greatest(-1.0,least(1.0,cov/sqrt(vx*vy))),
       (%s*floor(n/%s+0.5))::bigint
FROM stats WHERE n>=%s AND vx>0 AND vy>0
AND (SELECT min(n) FROM cells)>=%s
AND (SELECT count(DISTINCT xb) FROM cells)>=2 AND (SELECT count(DISTINCT yb) FROM cells)>=2
"""
    row = shadow.execute(sql, (*params, round_to, round_to, minimum, minimum_cell)).fetchone()
    return {"r": round(float(row[0]), 3), "sample_size": int(row[1])} if row else None


def categorical_comparison(shadow, namespace, *, x, x_edition, x_size, y, y_edition, y_size, unit="member"):
    """Latest journaled categorical answers, with editions and skips respected.

The SQL builds/joins the actual cohort and groups weighted cells. The existing
shared disclosure/rounding/Cramer's V primitive remains unchanged.
"""
    unit_check(unit)
    if (x == y or any(not isinstance(s, str) or not 1 <= len(s) <= 256 for s in (x, y, x_edition, y_edition))
            or any(type(n) is not int or not 1 <= n <= 64 for n in (x_size, y_size))):
        raise ValueError("Choose two bounded, versioned categorical variables.")
    base = """WITH eligible AS (""" + ELIGIBLE + """),latest AS (
 SELECT v.*,row_number() OVER(PARTITION BY v.owner_id,v.question_id ORDER BY
 COALESCE((h.payload#>>'{source_revision,$numberLong}')::bigint,
          (h.payload#>>'{source_revision,$numberInt}')::bigint,0) DESC,
 v.recorded_at DESC,v.event_id DESC) AS position
 FROM imladri_shadow.research_observations v
 JOIN imladri_shadow.history_events h USING(namespace,event_id)
 WHERE v.namespace=%s AND v.question_id IN (%s,%s)
),xs AS (SELECT owner_id,answer_code AS x FROM latest WHERE position=1 AND question_id=%s AND edition=%s
          AND NOT skipped AND answer_code>=0 AND answer_code<%s),
ys AS (SELECT owner_id,answer_code AS y FROM latest WHERE position=1 AND question_id=%s AND edition=%s
          AND NOT skipped AND answer_code>=0 AND answer_code<%s)"""
    params = [namespace, namespace, x, y, x, x_edition, x_size, y, y_edition, y_size]
    if unit == "member":
        observations = "SELECT xs.owner_id AS unit_id,x,y FROM xs JOIN ys USING(owner_id)"
    else:
        base += ",pairs AS (" + PAIRS + ")"
        params.append(namespace)
        directions = []
        if unit != "couple_invitee":
            directions.append("SELECT pair_id AS unit_id,x,y FROM pairs JOIN xs ON xs.owner_id=pairs.inviter JOIN ys ON ys.owner_id=pairs.invitee")
        if unit != "couple_inviter":
            directions.append("SELECT pair_id AS unit_id,x,y FROM pairs JOIN xs ON xs.owner_id=pairs.invitee JOIN ys ON ys.owner_id=pairs.inviter")
        observations = " UNION ALL ".join(directions)
    sql = base + ",observations AS (" + observations + "),weighted AS (SELECT *,1.0/count(*) OVER(PARTITION BY unit_id) AS w FROM observations) SELECT x,y,sum(w) FROM weighted GROUP BY x,y"
    counts = [[0.0] * y_size for _ in range(x_size)]
    for x_code, y_code, weight in shadow.execute(sql, params).fetchall():
        counts[x_code][y_code] = float(weight)
    from .research_trends import summarize
    return {"cohort": "latest_versioned_history", "unit": unit, **summarize(counts)}
