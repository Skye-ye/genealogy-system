"""Flask web app for the genealogy management system."""
from __future__ import annotations

import os
from functools import wraps

from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from db import cursor, execute, fetchall, fetchone, init_pool

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "dev-only")
init_pool()


# ---------------------------------------------------------------------------
# auth helpers
# ---------------------------------------------------------------------------
def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


@app.before_request
def load_user():
    g.user = None
    if "user_id" in session:
        g.user = fetchone(
            "SELECT id, username, email, is_admin FROM users WHERE id = %s",
            (session["user_id"],),
        )


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login", next=request.path))
        if not (g.user and g.user.get("is_admin")):
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def _user_can_access_genealogy(user_id: int, genealogy_id: int) -> str | None:
    """Return 'admin' / 'owner' / 'editor' / 'viewer' / None.

    Admins implicitly have full access to every genealogy regardless of
    ownership or collaborator status.
    """
    if g.user and g.user.get("is_admin"):
        # admins see everything as owners (so edit checks pass too)
        return "admin"
    row = fetchone(
        """
        SELECT 'owner' AS role FROM genealogies
         WHERE id = %s AND owner_user_id = %s
        UNION ALL
        SELECT role::text FROM genealogy_collaborators
         WHERE genealogy_id = %s AND user_id = %s
        LIMIT 1
        """,
        (genealogy_id, user_id, genealogy_id, user_id),
    )
    return row["role"] if row else None


def _require_access(genealogy_id: int, *, edit: bool = False) -> str:
    role = _user_can_access_genealogy(session["user_id"], genealogy_id)
    if role is None:
        abort(403)
    if edit and role == "viewer":
        abort(403)
    return role


# ---------------------------------------------------------------------------
# auth routes
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        row = fetchone(
            "SELECT id, password_hash FROM users WHERE username = %s",
            (username,),
        )
        if row and check_password_hash(row["password_hash"], password):
            session["user_id"] = row["id"]
            session["username"] = username
            return redirect(request.args.get("next") or url_for("dashboard"))
        flash("Invalid credentials", "error")
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"].strip()
        email = request.form.get("email", "").strip() or None
        password = request.form["password"]
        if not username or not password:
            flash("Username and password are required", "error")
        elif fetchone("SELECT 1 FROM users WHERE username = %s", (username,)):
            flash("Username already taken", "error")
        else:
            row = execute(
                "INSERT INTO users (username, password_hash, email) VALUES (%s, %s, %s) RETURNING id",
                (username, generate_password_hash(password), email),
            )
            session["user_id"] = row["id"]
            session["username"] = username
            return redirect(url_for("dashboard"))
    return render_template("register.html")


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# dashboard
# ---------------------------------------------------------------------------
@app.get("/dashboard")
@login_required
def dashboard():
    uid = session["user_id"]
    is_admin = bool(g.user and g.user.get("is_admin"))
    if is_admin:
        # Admins see every genealogy in the system.
        genealogies = fetchall(
            """
            SELECT g.*,
                   (SELECT count(*) FROM members m WHERE m.genealogy_id = g.id) AS n_members,
                   'admin' AS access
              FROM genealogies g
             ORDER BY g.id
            """
        )
    else:
        genealogies = fetchall(
            """
            SELECT g.*,
                   (SELECT count(*) FROM members m WHERE m.genealogy_id = g.id) AS n_members,
                   CASE WHEN g.owner_user_id = %(uid)s THEN 'owner' ELSE 'collab' END AS access
              FROM genealogies g
             WHERE g.owner_user_id = %(uid)s
                OR g.id IN (SELECT genealogy_id FROM genealogy_collaborators WHERE user_id = %(uid)s)
             ORDER BY g.id
            """,
            {"uid": uid},
        )
    if genealogies:
        gids = [row["id"] for row in genealogies]
        totals = fetchone(
            """
            SELECT count(*)                              AS n_total,
                   count(*) FILTER (WHERE gender = 'M')  AS n_male,
                   count(*) FILTER (WHERE gender = 'F')  AS n_female
              FROM members WHERE genealogy_id = ANY(%s)
            """,
            (gids,),
        )
    else:
        totals = {"n_total": 0, "n_male": 0, "n_female": 0}
    return render_template(
        "dashboard.html",
        genealogies=genealogies,
        totals=totals,
        is_admin=is_admin,
    )


# ---------------------------------------------------------------------------
# admin
# ---------------------------------------------------------------------------
@app.get("/admin")
@admin_required
def admin_panel():
    system_totals = fetchone(
        """
        SELECT count(*)                              AS n_total,
               count(*) FILTER (WHERE gender = 'M')  AS n_male,
               count(*) FILTER (WHERE gender = 'F')  AS n_female,
               count(DISTINCT genealogy_id)          AS n_genealogies
          FROM members
        """
    )
    user_rows = fetchall(
        """
        SELECT u.id, u.username, u.email, u.is_admin,
               (SELECT count(*) FROM genealogies WHERE owner_user_id = u.id)        AS n_owned,
               (SELECT count(*) FROM genealogy_collaborators WHERE user_id = u.id)  AS n_collab
          FROM users u ORDER BY u.id
        """
    )
    genealogy_rows = fetchall(
        """
        SELECT g.id, g.name, g.surname, g.compilation_date,
               u.username AS owner,
               (SELECT count(*) FROM members m WHERE m.genealogy_id = g.id) AS n_members,
               (SELECT max(generation) FROM members m WHERE m.genealogy_id = g.id) AS max_gen
          FROM genealogies g JOIN users u ON u.id = g.owner_user_id
         ORDER BY g.id
        """
    )
    return render_template(
        "admin.html",
        totals=system_totals,
        users=user_rows,
        genealogies=genealogy_rows,
    )


@app.post("/admin/users/<int:uid>/toggle_admin")
@admin_required
def admin_toggle_admin(uid: int):
    if uid == session["user_id"]:
        flash("不能取消自己的管理员权限", "error")
        return redirect(url_for("admin_panel"))
    execute("UPDATE users SET is_admin = NOT is_admin WHERE id = %s", (uid,))
    flash("管理员权限已更新", "success")
    return redirect(url_for("admin_panel"))


# ---------------------------------------------------------------------------
# genealogy CRUD
# ---------------------------------------------------------------------------
@app.route("/genealogies/new", methods=["GET", "POST"])
@login_required
def genealogy_new():
    if request.method == "POST":
        name = request.form["name"].strip()
        surname = request.form["surname"].strip()
        if not name or not surname:
            flash("Name and surname are required", "error")
        else:
            row = execute(
                "INSERT INTO genealogies (name, surname, owner_user_id) VALUES (%s, %s, %s) RETURNING id",
                (name, surname, session["user_id"]),
            )
            return redirect(url_for("genealogy_detail", gid=row["id"]))
    return render_template("genealogy_form.html", genealogy=None)


@app.get("/genealogies/<int:gid>")
@login_required
def genealogy_detail(gid: int):
    role = _require_access(gid)
    genealogy = fetchone("SELECT * FROM genealogies WHERE id = %s", (gid,))
    if not genealogy:
        abort(404)
    q = request.args.get("q", "").strip()
    if q:
        # Use trigram index for fuzzy match. Falls back to seq scan on short
        # CJK terms (see docs/index_strategy.md), still <10ms on 100K rows.
        members = fetchall(
            """
            SELECT id, name, gender, birth_year, death_year, generation
              FROM members
             WHERE genealogy_id = %s AND name ILIKE %s
             ORDER BY generation, id
             LIMIT 200
            """,
            (gid, f"%{q}%"),
        )
    else:
        members = fetchall(
            """
            SELECT id, name, gender, birth_year, death_year, generation
              FROM members
             WHERE genealogy_id = %s
             ORDER BY generation, id
             LIMIT 200
            """,
            (gid,),
        )
    n_total = fetchone(
        "SELECT count(*) AS n FROM members WHERE genealogy_id = %s", (gid,)
    )["n"]
    collaborators = fetchall(
        """
        SELECT u.id, u.username, gc.role
          FROM genealogy_collaborators gc JOIN users u ON u.id = gc.user_id
         WHERE gc.genealogy_id = %s ORDER BY u.username
        """,
        (gid,),
    )
    return render_template(
        "genealogy_detail.html",
        genealogy=genealogy, members=members, n_total=n_total, q=q, role=role,
        collaborators=collaborators,
    )


@app.post("/genealogies/<int:gid>/invite")
@login_required
def genealogy_invite(gid: int):
    _require_access(gid, edit=True)
    username = request.form["username"].strip()
    role = request.form.get("role", "editor")
    target = fetchone("SELECT id FROM users WHERE username = %s", (username,))
    if not target:
        flash(f"User '{username}' not found", "error")
    else:
        execute(
            """
            INSERT INTO genealogy_collaborators (genealogy_id, user_id, role)
            VALUES (%s, %s, %s::collab_role_t)
            ON CONFLICT (genealogy_id, user_id)
            DO UPDATE SET role = EXCLUDED.role
            """,
            (gid, target["id"], role),
        )
        flash(f"Invited {username} as {role}", "success")
    return redirect(url_for("genealogy_detail", gid=gid))


@app.post("/genealogies/<int:gid>/delete")
@login_required
def genealogy_delete(gid: int):
    role = _require_access(gid)
    if role != "owner":
        abort(403)
    execute("DELETE FROM genealogies WHERE id = %s", (gid,))
    flash("Genealogy deleted", "success")
    return redirect(url_for("dashboard"))


# ---------------------------------------------------------------------------
# member CRUD
# ---------------------------------------------------------------------------
@app.route("/genealogies/<int:gid>/members/new", methods=["GET", "POST"])
@login_required
def member_new(gid: int):
    _require_access(gid, edit=True)
    if request.method == "POST":
        name = request.form["name"].strip()
        gender = request.form["gender"]
        birth_year = request.form.get("birth_year") or None
        death_year = request.form.get("death_year") or None
        biography = request.form.get("biography", "").strip() or None
        father_id = request.form.get("father_id") or None
        mother_id = request.form.get("mother_id") or None
        try:
            row = execute(
                """
                INSERT INTO members (genealogy_id, name, gender, birth_year, death_year,
                                      biography, father_id, mother_id)
                VALUES (%s, %s, %s::gender_t, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (gid, name, gender, birth_year, death_year, biography, father_id, mother_id),
            )
            return redirect(url_for("member_detail", mid=row["id"]))
        except Exception as e:
            flash(f"Save failed: {e}", "error")
    return render_template("member_form.html", member=None, gid=gid,
                           father_name=None, mother_name=None)


@app.get("/members/<int:mid>")
@login_required
def member_detail(mid: int):
    m = fetchone("SELECT * FROM members WHERE id = %s", (mid,))
    if not m:
        abort(404)
    _require_access(m["genealogy_id"])
    # parents
    father = fetchone("SELECT id, name FROM members WHERE id = %s", (m["father_id"],)) if m["father_id"] else None
    mother = fetchone("SELECT id, name FROM members WHERE id = %s", (m["mother_id"],)) if m["mother_id"] else None
    # spouses + children (Q1 from spec)
    relations = fetchall(
        """
        SELECT rel.kind, other.id, other.name, other.gender,
               other.birth_year, other.death_year
          FROM (
              SELECT CASE WHEN m.member1_id = %(mid)s THEN m.member2_id
                          ELSE m.member1_id END AS other_id,
                     'spouse' AS kind, m.married_year AS yr
                FROM marriages m
               WHERE m.member1_id = %(mid)s OR m.member2_id = %(mid)s
              UNION ALL
              SELECT c.id, 'child', c.birth_year FROM members c
               WHERE c.father_id = %(mid)s OR c.mother_id = %(mid)s
          ) rel JOIN members other ON other.id = rel.other_id
         ORDER BY (rel.kind = 'spouse') DESC, rel.yr NULLS LAST, other.id
        """,
        {"mid": mid},
    )
    # cross-genealogy "same person" links
    links = fetchall(
        """
        SELECT l.id AS link_id, l.note,
               other.id, other.name, other.gender, other.birth_year, other.death_year,
               gen.name AS genealogy_name, gen.id AS genealogy_id
          FROM member_links l
          JOIN members other
            ON other.id = CASE WHEN l.member_a_id = %(mid)s THEN l.member_b_id
                              ELSE l.member_a_id END
          JOIN genealogies gen ON gen.id = other.genealogy_id
         WHERE l.member_a_id = %(mid)s OR l.member_b_id = %(mid)s
         ORDER BY gen.id, other.id
        """,
        {"mid": mid},
    )
    return render_template(
        "member_detail.html",
        m=m, father=father, mother=mother, relations=relations, links=links,
    )


@app.post("/members/<int:mid>/links/add")
@login_required
def member_link_add(mid: int):
    """Create a cross-genealogy 'same person' link. Admin-only for now."""
    if not (g.user and g.user.get("is_admin")):
        abort(403)
    m = fetchone("SELECT * FROM members WHERE id = %s", (mid,))
    if not m:
        abort(404)
    other_id_raw = request.form.get("other_id", "").strip()
    note = request.form.get("note", "").strip() or None
    if not other_id_raw.isdigit():
        flash("请输入有效的成员 ID", "error")
        return redirect(url_for("member_detail", mid=mid))
    other_id = int(other_id_raw)
    if other_id == mid:
        flash("不能链接到自己", "error")
        return redirect(url_for("member_detail", mid=mid))
    other = fetchone("SELECT id, genealogy_id FROM members WHERE id = %s", (other_id,))
    if not other:
        flash("目标成员不存在", "error")
        return redirect(url_for("member_detail", mid=mid))
    if other["genealogy_id"] == m["genealogy_id"]:
        flash("跨族谱链接需选择不同族谱中的成员", "error")
        return redirect(url_for("member_detail", mid=mid))
    a_id, b_id = sorted([mid, other_id])
    try:
        execute(
            """INSERT INTO member_links (member_a_id, member_b_id, link_type, note)
                    VALUES (%s, %s, 'same_person', %s)
               ON CONFLICT (member_a_id, member_b_id) DO UPDATE
                       SET note = COALESCE(EXCLUDED.note, member_links.note)""",
            (a_id, b_id, note),
        )
        flash("已添加跨族谱链接", "success")
    except Exception as e:
        flash(f"添加失败：{e}", "error")
    return redirect(url_for("member_detail", mid=mid))


@app.post("/members/<int:mid>/links/<int:link_id>/delete")
@login_required
def member_link_delete(mid: int, link_id: int):
    if not (g.user and g.user.get("is_admin")):
        abort(403)
    execute("DELETE FROM member_links WHERE id = %s", (link_id,))
    flash("链接已删除", "success")
    return redirect(url_for("member_detail", mid=mid))


@app.route("/members/<int:mid>/edit", methods=["GET", "POST"])
@login_required
def member_edit(mid: int):
    m = fetchone("SELECT * FROM members WHERE id = %s", (mid,))
    if not m:
        abort(404)
    _require_access(m["genealogy_id"], edit=True)
    if request.method == "POST":
        try:
            execute(
                """
                UPDATE members SET name=%s, gender=%s::gender_t,
                                   birth_year=%s, death_year=%s,
                                   biography=%s, father_id=%s, mother_id=%s
                 WHERE id = %s
                """,
                (
                    request.form["name"].strip(),
                    request.form["gender"],
                    request.form.get("birth_year") or None,
                    request.form.get("death_year") or None,
                    request.form.get("biography", "").strip() or None,
                    request.form.get("father_id") or None,
                    request.form.get("mother_id") or None,
                    mid,
                ),
            )
            return redirect(url_for("member_detail", mid=mid))
        except Exception as e:
            flash(f"Save failed: {e}", "error")
    father_name = None
    mother_name = None
    if m.get("father_id"):
        f = fetchone("SELECT name FROM members WHERE id = %s", (m["father_id"],))
        father_name = f["name"] if f else None
    if m.get("mother_id"):
        mo = fetchone("SELECT name FROM members WHERE id = %s", (m["mother_id"],))
        mother_name = mo["name"] if mo else None
    return render_template("member_form.html", member=m, gid=m["genealogy_id"],
                           father_name=father_name, mother_name=mother_name)


@app.post("/members/<int:mid>/delete")
@login_required
def member_delete(mid: int):
    m = fetchone("SELECT genealogy_id FROM members WHERE id = %s", (mid,))
    if not m:
        abort(404)
    _require_access(m["genealogy_id"], edit=True)
    execute("DELETE FROM members WHERE id = %s", (mid,))
    flash("Member deleted", "success")
    return redirect(url_for("genealogy_detail", gid=m["genealogy_id"]))


# ---------------------------------------------------------------------------
# tree preview, ancestor query, kinship query
# ---------------------------------------------------------------------------
@app.get("/members/<int:mid>/tree")
@login_required
def member_tree(mid: int):
    m = fetchone("SELECT * FROM members WHERE id = %s", (mid,))
    if not m:
        abort(404)
    _require_access(m["genealogy_id"])
    max_depth = int(request.args.get("depth", 4))
    rows = fetchall(
        """
        WITH RECURSIVE descendants (id, name, gender, birth_year, parent_id, depth) AS (
            SELECT id, name, gender, birth_year, NULL::bigint, 0 FROM members WHERE id = %(mid)s
            UNION ALL
            SELECT c.id, c.name, c.gender, c.birth_year,
                   CASE WHEN c.father_id = d.id THEN c.father_id ELSE c.mother_id END,
                   d.depth + 1
              FROM descendants d
              JOIN members c ON c.father_id = d.id OR c.mother_id = d.id
             WHERE d.depth < %(maxd)s
        )
        SELECT id, name, gender, birth_year, parent_id, depth FROM descendants ORDER BY depth, id
        """,
        {"mid": mid, "maxd": max_depth},
    )
    # build a parent->children map for the template
    children_of: dict[int | None, list] = {}
    for r in rows:
        children_of.setdefault(r["parent_id"], []).append(r)
    root = next((r for r in rows if r["depth"] == 0), None)
    return render_template(
        "tree.html", root=root, children_of=children_of,
        m=m, max_depth=max_depth,
    )


@app.get("/members/<int:mid>/ancestors")
@login_required
def member_ancestors(mid: int):
    m = fetchone("SELECT * FROM members WHERE id = %s", (mid,))
    if not m:
        abort(404)
    _require_access(m["genealogy_id"])
    rows = fetchall(
        """
        WITH RECURSIVE ancestors (id, name, gender, birth_year, death_year,
                                  relation, depth, path_ids) AS (
            SELECT p.id, p.name, p.gender, p.birth_year, p.death_year,
                   CASE WHEN m.father_id = p.id THEN 'father' ELSE 'mother' END,
                   1, ARRAY[p.id]
              FROM members m JOIN members p
                ON p.id = m.father_id OR p.id = m.mother_id
             WHERE m.id = %(mid)s
            UNION ALL
            SELECT pp.id, pp.name, pp.gender, pp.birth_year, pp.death_year,
                   a.relation || ' -> ' ||
                       CASE WHEN a2.father_id = pp.id THEN 'father' ELSE 'mother' END,
                   a.depth + 1, a.path_ids || pp.id
              FROM ancestors a
              JOIN members a2 ON a2.id = a.id
              JOIN members pp ON pp.id = a2.father_id OR pp.id = a2.mother_id
             WHERE pp.id <> ALL(a.path_ids) AND a.depth < 50
        )
        SELECT depth, id, name, gender, birth_year, death_year, relation
          FROM ancestors ORDER BY depth, id
        """,
        {"mid": mid},
    )
    return render_template("ancestors.html", m=m, ancestors=rows)


# ---------------------------------------------------------------------------
# Bidirectional BFS for kinship paths.
#
# The naive recursive CTE we used before explores O(b^d) nodes for a path of
# length d (b ≈ 5: father + mother + ~2 children + 1 spouse). Past depth ~10
# this becomes uncomfortably slow.
#
# Bidirectional BFS expands from both endpoints simultaneously and stops
# when the frontiers meet — total work is O(b^(d/2)). For depth 20 that's
# the difference between ~10M nodes and ~3000.
#
# We do one expansion = one SQL roundtrip ("neighbors of these N ids"),
# then meet-check happens in Python. Each node remembers its predecessor on
# its own side so we can stitch the path back together.
# ---------------------------------------------------------------------------

# Each side's "edge label" describes the relationship of the *next* node
# from the *current* node's perspective. When we traverse the B-side path
# in reverse to stitch the answer, we invert the labels.
_INVERT_EDGE = {
    "father": "child",
    "mother": "child",
    "child": "parent",
    "spouse": "spouse",
    "same_person": "same_person",  # symmetric — bridges two records of the same real person
}


def _expand_frontier(cur, frontier_ids: list[int]) -> list[dict]:
    """Return all (from_id, next_id, next_name, edge) edges out of frontier_ids.

    Uses idx_members_father_id, idx_members_mother_id, marriages_unique_pair,
    idx_marriages_member2 — all of which are bitmap-scannable for an
    `id = ANY(%s)` predicate, so this is one fast roundtrip per BFS layer.
    """
    cur.execute(
        """
        SELECT me.id AS from_id, p.id AS next_id, p.name AS next_name, 'father' AS edge
          FROM members me JOIN members p ON p.id = me.father_id
         WHERE me.id = ANY(%(ids)s) AND me.father_id IS NOT NULL
        UNION ALL
        SELECT me.id, p.id, p.name, 'mother'
          FROM members me JOIN members p ON p.id = me.mother_id
         WHERE me.id = ANY(%(ids)s) AND me.mother_id IS NOT NULL
        UNION ALL
        SELECT par.id AS from_id, c.id, c.name, 'child'
          FROM members c JOIN members par
            ON par.id = c.father_id OR par.id = c.mother_id
         WHERE par.id = ANY(%(ids)s)
        UNION ALL
        SELECT mar.member1_id, m2.id, m2.name, 'spouse'
          FROM marriages mar JOIN members m2 ON m2.id = mar.member2_id
         WHERE mar.member1_id = ANY(%(ids)s)
        UNION ALL
        SELECT mar.member2_id, m1.id, m1.name, 'spouse'
          FROM marriages mar JOIN members m1 ON m1.id = mar.member1_id
         WHERE mar.member2_id = ANY(%(ids)s)
        UNION ALL
        -- cross-genealogy "same person" links (symmetric)
        SELECT l.member_a_id, mb.id, mb.name, 'same_person'
          FROM member_links l JOIN members mb ON mb.id = l.member_b_id
         WHERE l.member_a_id = ANY(%(ids)s)
        UNION ALL
        SELECT l.member_b_id, ma.id, ma.name, 'same_person'
          FROM member_links l JOIN members ma ON ma.id = l.member_a_id
         WHERE l.member_b_id = ANY(%(ids)s)
        """,
        {"ids": frontier_ids},
    )
    return cur.fetchall()


def _bidirectional_kinship(a_id: int, b_id: int, *, max_depth: int = 30) -> list[dict] | None:
    """Find a shortest kinship path between a_id and b_id using bidirectional BFS.

    Returns a list of {"id", "name", "edge"} hops (with "start" as the first
    edge label), or None if no path exists within max_depth hops.

    max_depth is the *total* path length cap; each side searches up to
    ceil(max_depth / 2) layers.
    """
    if a_id == b_id:
        with cursor() as cur:
            cur.execute("SELECT name FROM members WHERE id = %s", (a_id,))
            row = cur.fetchone()
        return [{"id": a_id, "name": row["name"] if row else "", "edge": "start"}]

    # node_id -> (predecessor_id_on_this_side | None, edge_from_predecessor, name)
    visited_a: dict[int, tuple[int | None, str, str]] = {a_id: (None, "start", "")}
    visited_b: dict[int, tuple[int | None, str, str]] = {b_id: (None, "start", "")}

    # Cache names so we can fill them in on path reconstruction even if the
    # node was never returned as next_id (true only for the two endpoints).
    with cursor() as cur:
        cur.execute(
            "SELECT id, name FROM members WHERE id = ANY(%s)", ([a_id, b_id],)
        )
        for r in cur.fetchall():
            tgt = visited_a if r["id"] == a_id else visited_b
            tgt[r["id"]] = (None, "start", r["name"])

    frontier_a = [a_id]
    frontier_b = [b_id]
    layers_a = 0  # how many times A's side has been expanded
    layers_b = 0

    # Each loop iteration expands ONE side by one layer. The found path length
    # equals layers_a + layers_b at the moment of meeting. So to find paths up
    # to max_depth hops we need at most max_depth iterations.
    with cursor() as cur:
        for _ in range(max_depth):
            # Always expand the smaller frontier — it minimizes total work.
            # Stop expanding a side once it's already gone deep enough that
            # any further expansion would only find paths > max_depth.
            expand_a = (len(frontier_a) <= len(frontier_b)
                        and layers_a + layers_b < max_depth)
            if expand_a:
                frontier_a, meet = _bfs_step(cur, frontier_a, visited_a, visited_b)
                layers_a += 1
                if meet is not None:
                    return _stitch(meet, visited_a, visited_b)
                if not frontier_a:
                    return None
            else:
                frontier_b, meet = _bfs_step(cur, frontier_b, visited_b, visited_a)
                layers_b += 1
                if meet is not None:
                    return _stitch(meet, visited_a, visited_b)
                if not frontier_b:
                    return None
    return None


def _bfs_step(cur, frontier, visited_self, visited_other):
    """Expand one BFS layer. Returns (new_frontier, meeting_node_id_or_None)."""
    if not frontier:
        return [], None
    edges = _expand_frontier(cur, frontier)
    new_frontier_set: dict[int, tuple[int, str, str]] = {}
    for e in edges:
        nid = e["next_id"]
        if nid in visited_self:
            continue
        # Record predecessor on this side
        new_frontier_set[nid] = (e["from_id"], e["edge"], e["next_name"])
    # Commit the new layer
    for nid, info in new_frontier_set.items():
        visited_self[nid] = info
    # Meet check — does any new node already exist in the other side's visited?
    for nid in new_frontier_set:
        if nid in visited_other:
            return list(new_frontier_set.keys()), nid
    return list(new_frontier_set.keys()), None


def _stitch(meet_id, visited_a, visited_b):
    """Reconstruct A → meet → B as a list of {id, name, edge}.

    The 'edge' on each hop describes the relationship to that node from the
    previous one, in the forward direction A → ... → B.
    """
    # If the meet was discovered while expanding B's side, the caller passed
    # the same meet_id but it lives in visited_b too. We always trace meet→a
    # via visited_a, and meet→b via visited_b.
    # Walk meet → A via visited_a (collect in reverse)
    a_chain: list[tuple[int, str, str]] = []  # (id, edge_from_prev, name)
    cur = meet_id
    while cur is not None:
        pred, edge, name = visited_a.get(cur, (None, "?", ""))
        a_chain.append((cur, edge, name))
        cur = pred
    a_chain.reverse()  # now A → meet

    # Walk meet → B via visited_b
    b_chain: list[tuple[int, str, str]] = []
    cur = meet_id
    while cur is not None:
        pred, edge, name = visited_b.get(cur, (None, "?", ""))
        b_chain.append((cur, edge, name))
        cur = pred
    # b_chain now goes meet → B (each element's `edge` describes hop FROM the
    # *next* node TO this one, since we walked predecessor links). We want
    # edges in the forward direction meet → B, so we invert each label and
    # shift them by one.

    path: list[dict] = [{"id": a_chain[0][0], "name": a_chain[0][2], "edge": "start"}]
    for nid, edge, name in a_chain[1:]:
        path.append({"id": nid, "name": name, "edge": edge})

    # b_chain[0] is meet (already in path via a_chain). For each forward hop
    # b_chain[i-1] → b_chain[i], the relationship label was stored on
    # b_chain[i-1] during B's outward walk ("b_chain[i-1] is b_chain[i]'s X").
    # Inverting that gives the forward edge ("b_chain[i] is b_chain[i-1]'s INV(X)").
    for i in range(1, len(b_chain)):
        nid, _stored_edge, name = b_chain[i]
        pred_edge = b_chain[i - 1][1]
        forward_edge = _INVERT_EDGE.get(pred_edge, pred_edge)
        path.append({"id": nid, "name": name, "edge": forward_edge})

    return path


# ---------------------------------------------------------------------------
# Cross-genealogy member search — like the typeahead but with a real results
# table, filters, and direct links into each member's detail page.
# ---------------------------------------------------------------------------
@app.get("/search")
@login_required
def cross_search():
    q = request.args.get("q", "").strip()
    gender = request.args.get("gender", "").strip()
    status = request.args.get("status", "").strip()  # 'alive' | 'deceased' | ''
    born_from = request.args.get("born_from", "").strip()
    born_to = request.args.get("born_to", "").strip()
    is_admin = bool(g.user and g.user.get("is_admin"))

    results: list[dict] = []
    facet_by_genealogy: list[dict] = []
    elapsed_ms = None
    total_count = 0

    has_filter = any([q, gender, status, born_from, born_to])
    if has_filter:
        where: list[str] = []
        params: list = []

        # Access scope
        if not is_admin:
            where.append(
                "m.genealogy_id IN ("
                "  SELECT id FROM genealogies WHERE owner_user_id = %s"
                "  UNION SELECT genealogy_id FROM genealogy_collaborators WHERE user_id = %s"
                ")"
            )
            params.extend([session["user_id"], session["user_id"]])

        if q:
            where.append("m.name ILIKE %s")
            params.append(f"%{q}%")
        if gender in ("M", "F"):
            where.append("m.gender = %s::gender_t")
            params.append(gender)
        if status == "alive":
            where.append("m.death_year IS NULL")
        elif status == "deceased":
            where.append("m.death_year IS NOT NULL")
        if born_from.isdigit():
            where.append("m.birth_year >= %s")
            params.append(int(born_from))
        if born_to.isdigit():
            where.append("m.birth_year <= %s")
            params.append(int(born_to))

        where_clause = " AND ".join(where) if where else "TRUE"

        import time
        t0 = time.perf_counter()
        results = fetchall(
            f"""
            SELECT m.id, m.name, m.gender, m.birth_year, m.death_year,
                   m.generation, m.genealogy_id,
                   gen.name AS genealogy_name, gen.surname
              FROM members m JOIN genealogies gen ON gen.id = m.genealogy_id
             WHERE {where_clause}
             ORDER BY m.genealogy_id, m.generation, m.id
             LIMIT 200
            """,
            tuple(params),
        )
        # Count total (capped at 5000 so we don't pay for huge scans on
        # broad queries — show "5000+" if at the cap)
        cnt_row = fetchone(
            f"""
            SELECT count(*) AS n FROM (
                SELECT 1 FROM members m JOIN genealogies gen ON gen.id = m.genealogy_id
                 WHERE {where_clause} LIMIT 5000
            ) sub
            """,
            tuple(params),
        )
        total_count = cnt_row["n"] if cnt_row else 0

        # Per-genealogy facet (counts of matches in each genealogy)
        facet_by_genealogy = fetchall(
            f"""
            SELECT gen.id, gen.name, gen.surname, count(*) AS n
              FROM members m JOIN genealogies gen ON gen.id = m.genealogy_id
             WHERE {where_clause}
             GROUP BY gen.id, gen.name, gen.surname
             ORDER BY n DESC, gen.id
            """,
            tuple(params),
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000

    return render_template(
        "search.html",
        q=q, gender=gender, status=status,
        born_from=born_from, born_to=born_to,
        results=results, total_count=total_count,
        facet_by_genealogy=facet_by_genealogy,
        elapsed_ms=elapsed_ms, has_filter=has_filter,
    )


@app.route("/kinship", methods=["GET", "POST"])
@login_required
def kinship():
    """Find a kinship path between two members via bidirectional BFS."""
    a_id = request.values.get("a")
    b_id = request.values.get("b")
    max_depth = int(request.values.get("max_depth", 30))
    max_depth = max(2, min(max_depth, 60))  # clamp
    result = None
    err = None
    elapsed_ms = None
    a_name = b_name = None
    if a_id and b_id:
        try:
            a_id, b_id = int(a_id), int(b_id)
        except ValueError:
            err = "Member ids must be integers"
        else:
            ma = fetchone("SELECT * FROM members WHERE id = %s", (a_id,))
            mb = fetchone("SELECT * FROM members WHERE id = %s", (b_id,))
            if not ma or not mb:
                err = "One of the member ids was not found"
            else:
                _require_access(ma["genealogy_id"])
                _require_access(mb["genealogy_id"])
                a_name, b_name = ma["name"], mb["name"]
                # Cross-genealogy is now allowed: kinship BFS can hop through
                # `member_links.same_person` edges. If no such bridge exists,
                # the search will return None just like an in-genealogy miss.
                import time
                t0 = time.perf_counter()
                path = _bidirectional_kinship(a_id, b_id, max_depth=max_depth)
                elapsed_ms = (time.perf_counter() - t0) * 1000
                result = {"path": path}
    return render_template(
        "kinship.html",
        a=a_id, b=b_id, a_name=a_name, b_name=b_name,
        result=result, err=err,
        max_depth=max_depth, elapsed_ms=elapsed_ms,
    )


# ---------------------------------------------------------------------------
# autocomplete (used by member form parent pickers)
# ---------------------------------------------------------------------------
@app.get("/api/members/search")
@login_required
def api_member_search():
    """Typeahead backend.

    Optional params:
      - q (required): substring of the member name (ILIKE %q%)
      - genealogy_id: scope to one genealogy (required when used from a member
        form; the trigger constraints make father/mother picks only meaningful
        within the same genealogy as the new child)
      - gender: 'M' or 'F' (used when picking a parent of the right sex)
    Without genealogy_id, returns matches across every genealogy the user can
    access. Admins see everything.
    """
    q = request.args.get("q", "").strip()
    if not q:
        return {"results": []}
    gid = request.args.get("genealogy_id")
    gender_filter = request.args.get("gender")
    is_admin = bool(g.user and g.user.get("is_admin"))

    where = ["m.name ILIKE %s"]
    params: list = [f"%{q}%"]
    if gid:
        gid_int = int(gid)
        _require_access(gid_int)
        where.append("m.genealogy_id = %s")
        params.append(gid_int)
    elif not is_admin:
        where.append(
            "m.genealogy_id IN ("
            "  SELECT id FROM genealogies WHERE owner_user_id = %s"
            "  UNION SELECT genealogy_id FROM genealogy_collaborators WHERE user_id = %s"
            ")"
        )
        params.extend([session["user_id"], session["user_id"]])
    if gender_filter in ("M", "F"):
        where.append("m.gender = %s::gender_t")
        params.append(gender_filter)
    where_clause = " AND ".join(where)

    if gid:
        # Scoped to a single genealogy — straight result set is fine.
        sql = f"""
            SELECT m.id, m.name, m.gender, m.birth_year, m.death_year,
                   m.genealogy_id, gen.name AS genealogy_name, gen.surname
              FROM members m JOIN genealogies gen ON gen.id = m.genealogy_id
             WHERE {where_clause}
             ORDER BY m.id
             LIMIT 20
        """
    else:
        # Cross-genealogy — stratify so the big genealogy doesn't drown out
        # the rest. At most 3 hits per genealogy, max 30 total.
        sql = f"""
            WITH matches AS (
                SELECT m.id, m.name, m.gender, m.birth_year, m.death_year,
                       m.genealogy_id, gen.name AS genealogy_name, gen.surname,
                       ROW_NUMBER() OVER (PARTITION BY m.genealogy_id ORDER BY m.id) AS rn
                  FROM members m JOIN genealogies gen ON gen.id = m.genealogy_id
                 WHERE {where_clause}
            )
            SELECT id, name, gender, birth_year, death_year,
                   genealogy_id, genealogy_name, surname
              FROM matches WHERE rn <= 3
             ORDER BY genealogy_id, id LIMIT 30
        """
    rows = fetchall(sql, tuple(params))
    return {"results": [dict(r) for r in rows]}


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)),
            debug=os.environ.get("FLASK_DEBUG") == "1")
