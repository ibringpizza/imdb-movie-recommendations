import asyncio, psycopg

class Database:
    def __init__(self, conn):
        self.conn = conn
    
    async def write_title(self, data):
        ###print('WRITING TITLES PSYCOPG')
        async with self.conn.cursor() as cur:
            await cur.execute('INSERT INTO titles (tt, title, genres, plot, aggregateRating, votingCount, keywords) VALUES (%s, %s, %s, %s, %s, %s, %s) on conflict (tt) DO UPDATE SET tt=%s, title=%s, genres=%s, plot=%s, aggregateRating=%s, votingCount=%s, keywords=%s RETURNING tt', data*2)
        ###print(data)

    async def write_review(self, data):
        ###print('WRITING REVIEWS PSYCOPG')
        async with self.conn.cursor() as cur:
            await cur.execute("INSERT INTO reviews (tt, uid, rid, rating, ts, rtitle) VALUES (%s, %s, %s, %s, %s, %s) on conflict (rid) do nothing RETURNING rid;", data)
        ###print(data)
    
    async def review_exists(self, rid):
        async with self.conn.cursor() as cur:
            await cur.execute("select (rid) from reviews where rid=%s", [rid])
            res = cur.fetchone()
        return bool(res)

    async def find_review(self, uid, tt):
        urids = await(self.get_user_rids(uid))
        async with self.conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            await cur.execute('select * from reviews where rid = ANY(%s) and tt=%s', [urids, tt])
            res = await cur.fetchone()
        return res

    async def get_review(self, rid):
        async with self.conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            await cur.execute('select * from reviews where rid = %s', [rid])
            res = await cur.fetchone()
        return res
    
    async def check_rid_exists(self, rid):
        async with self.conn.cursor() as cur:
            await cur.execute('select rid from reviews where rid = %s', [rid])
            res = await cur.fetchone()
        return bool(res)
    
    async def add_uid_reviewed(self, tt, uid): #add uid to title's reviewed users list without duplicates
        async with self.conn.cursor() as cur:
            await cur.execute('UPDATE titles SET uids = array_append(uids, %s) WHERE tt=%s AND %s <> ALL(uids);', (uid, tt, uid))
    
    async def get_all_tids(self): #gets all title ids, including information not yet scraped
        async with self.conn.cursor() as cur:
            await cur.execute('SELECT tt FROM title_users')
            res = await cur.fetchall()
        return [e[0] for e in res]
    
    async def get_title_uids(self, tt):
        async with self.conn.cursor() as cur:
            await cur.execute('SELECT uids FROM title_users WHERE tt = %s', [tt])
            res = await cur.fetchall()
        return res[0][0] if res and res[0] and res[0][0] else []

    async def check_ttl_exist(self, ttl):
        async with self.conn.cursor() as cur:
            ###await cur.execute('SELECT tt FROM titles WHERE tt = ANY(%s)', [ttl])
            ### Have to check if title info data has been inserted instead of making new database to track titles with no info (title/timestamp only)
            await cur.execute('SELECT plot FROM titles WHERE tt=ANY(%s)', [ttl])
            res = await cur.fetchall()
        return len([e for e in res if e[0]]) == len(ttl)
    
    async def get_all_titles(self): #get all title information
        async with self.conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            await cur.execute('SELECT * FROM titles;')
            res = await cur.fetchall()
        return res
    
    async def get_list_titles(self, ttl): #get all title information for list of title ids
        async with self.conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            await cur.execute('SELECT * FROM titles WHERE tt = ANY(%s);', [ttl])
            res = await cur.fetchall()
        return res
    
    async def get_title_info(self, tt):
        async with self.conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            await cur.execute('SELECT * FROM titles WHERE tt = %s;', [tt])
            res = await cur.fetchone()
        return res
    
    async def get_title_info_attr(self, tt, attrs):
        cols = f'({", ".join(attrs)})'
        async with self.conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            await cur.execute(f'SELECT {cols} FROM titles WHERE tt = %s;', [tt])
            res = await cur.fetchone()
        return res
    
    async def set_title_info_attr(self, tt, attrs, vals):
        query_str = ', '.join([f'{attr}=%s' for attr in attrs])
        cols = f'(tt, {", ".join(attrs)})'
        valstr = f'({",".join(["%s" for i in range(len(vals)+1)])})'
        async with self.conn.cursor() as cur:
            await cur.execute(f'insert into titles {cols} values {valstr} on conflict (tt) do update set {query_str};', [tt, *vals, *vals])

    async def bulk_set_seen(self, ttl):
        async with self.conn.cursor() as cur:
            await cur.execute('update titles SET seen = true where tt=ANY(%s)', [ttl])

    async def add_title_users(self, tt, uid): #add tt and uid entry or uid to existing tt entry in title_users table
        async with self.conn.cursor() as cur:
            await cur.execute("insert into title_users (tt, uids) values (%s, %s) on conflict (tt) do update set uids = array_append(title_users.uids, %s) where %s <> ALL(title_users.uids);", (tt, [uid], uid, uid))

    async def add_rid_to_users(self, uid, rid): #add tt and uid entry or uid to existing tt entry in users table
        try:
            async with self.conn.cursor() as cur:
                await cur.execute("insert into users (uid, rids) values (%s, %s) on conflict (uid) do update set rids = array_append(users.rids, %s) where %s <> ALL(users.rids);", (uid, [rid], rid, rid))
        except Exception as e:
            print(e)
    async def add_user_scraped_ts(self, uid, ts):
        try:
            async with self.conn.cursor() as cur:
                await cur.execute("insert into users (uid, last_scraped_ts) values (%s, %s) on conflict (uid) do update set last_scraped_ts = %s;", (uid, ts, ts))
        except Exception as e:
            print(e)

    async def add_username(self, uid, username):
        async with self.conn.cursor() as cur:
            await cur.execute("insert into users (uid, username) values (%s, %s) on conflict (uid) do update set username = %s;", (uid, username, username))

    async def get_username(self, uid):
        async with self.conn.cursor() as cur:
            await cur.execute("select username from users where uid=%s", [uid])
            res = await cur.fetchone()
        return res[0]

    async def remove_user(self, uid):
        async with self.conn.cursor() as cur:
            await cur.execute("delete from users where uid=%s", [uid])
    
    async def remove_added_user(self, uid):
        async with self.conn.cursor() as cur:
            await cur.execute("update users set added=false where uid=%s", [uid])
        
    async def add_user(self, uid):
        try:
            async with self.conn.cursor() as cur:
                await cur.execute("update users set added=true where uid=%s", [uid])
        except Exception as e:
            print(e)

    async def check_rid_in_users(self, uid, rid):
        async with self.conn.cursor() as cur:
            await cur.execute("select * from users where uid=%s and %s in rids", [uid, rid])
            res = cur.fetchone()
        return bool(res)
    
    async def user_exists(self, uid):
        async with self.conn.cursor() as cur:
            await cur.execute("select (uid) from users where uid=%s", [uid])
            res = cur.fetchone()
        return bool(res)
    
    async def check_user_added(self, uid):
        async with self.conn.cursor() as cur:
            await cur.execute("select (added) from users where uid=%s", [uid])
            res = await cur.fetchone()
        return res[0]

    async def get_user_ts(self, uid):
        async with self.conn.cursor() as cur:
            await cur.execute("select (last_scraped_ts) from users where uid=%s", [uid])
            res = await cur.fetchone()
        return res[0] if res[0] else 0
    
    async def get_all_uids(self):
        async with self.conn.cursor() as cur:
            await cur.execute('SELECT uid FROM users')
            res = await cur.fetchall()
        return [e[0] for e in res if e[0]]
    
    async def get_all_added_uids(self):
        async with self.conn.cursor() as cur:
            await cur.execute('SELECT uid FROM users WHERE added=true')
            res = await cur.fetchall()
        return [e[0] for e in res if e[0]]

    async def get_user_rids(self, uid):
        async with self.conn.cursor() as cur:
            await cur.execute('select rids from users where uid=%s', [uid])
            res = await cur.fetchall()
        return res[0][0] if res and res[0] and res[0][0] else []
    
    async def get_user_rids_ttl(self, uid, ttl): #get list of user review ids for titles from ttl
        urids = await self.get_user_rids(uid)
        async with self.conn.cursor() as cur:
            await cur.execute('select rid from reviews where rid=ANY(%s) and tt=ANY(%s)', [urids, ttl])
            res = await cur.fetchall()
        return [e[0] for e in res if e[0]]

