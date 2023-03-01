import asyncio, re, datetime, ujson, copy, urllib, math
from aiohttp import ClientSession
from leakybucket import AsyncLimiter
from bs4 import BeautifulSoup as bs
import traceback
import functools
from collections import deque
from time import monotonic, time
from time import time, sleep
from database import Database

class Reviews:

    errs = {
        0: 'FAILED',
        1: 'PARTIAL',
        2: 'SUCCESS'
    }

    MAX_INTERVAL = 30
    RETRY_HISTORY = 3

    def __init__(self, db):
        self.db = Database(db)
        self.bucket = AsyncLimiter(1, 2)
        self.mi_pq = asyncio.PriorityQueue()
        self.recommendations = asyncio.PriorityQueue()
        self.fetching_urs = set() #added to from get_user_reviews and checked in find_recommendations to avoid starting mutliple tasks with same ur
        self.loop = asyncio.get_event_loop()
        asyncio.create_task(self.all_recommendations()) #called again in app.wsep() on new connection
        self.all_rec_last_ts = 0
        self.update_queue = asyncio.Queue()
        self.get_updates = True
        self.update_data_done = False
        asyncio.run_coroutine_threadsafe(self.get_movie_info(), self.loop)
        asyncio.run_coroutine_threadsafe(self.find_recommendations(), self.loop)

    async def add_seen(self, tt):
        await self.db.bulk_set_seen([tt])

    async def add_watch(self, ur):
        p = ur.split('|')
        if p[0] == 'ADD':
            await self.db.add_user_scraped_ts(p[1], 0)
            await self.db.add_user(p[1])
        else:
            await self.db.remove_added_user(p[1])
        print('add watch ' + ur)

    async def get_movie_info(self):
        print('getting movie info')
##        bucket = AsyncLimiter(1, 2)
        while True:
            try:
                qs = self.mi_pq.qsize()
                size = min(20, qs + int(bool(not qs))) #+1 if qs == 0
                print(size)
                s = time()
                updates = []
                recommended = {}
                c = 0
                while c < size: #while len(updates) < 20 and time.time()+0.5 > s:
                    try:
                        val = await asyncio.wait_for(self.mi_pq.get(), 1)
                    except:
                        continue
                    if await self.db.check_ttl_exist([val[2]]):
                        if len(await self.db.get_title_uids(val[2])) >= 2:
                            await self.recommendations.put((0, time(), await self.db.get_title_info(val[2])))
                            await self.recommendations.put((0, time(), {'tt': val[2], 'update': True}))
                            qs = self.mi_pq.qsize()
                            size = min(20, qs + int(bool(not qs))) #+1 if qs == 0
                        continue
                    else:
                        c += 1
                    updates.append(val[2])
                ttstr = urllib.parse.quote_plus(','.join([f'"{e}"' for e in updates]))
                query = f'%7Btitles%28ids%3A%20%5B{ttstr}%5D%29%20%7B%20%20titleText%7B%20%20%20%20text%20%20%7D%20%20titleGenres%20%7B%20%20%20%20genres%20%7B%20%20%20%20%20%20genre%20%7B%20%20%20%20%20%20%20%20text%20%20%20%20%20%20%7D%20%20%20%20%7D%20%20%7D%20%20plots%28first%3A%201%29%20%7B%20%20%20%20edges%20%7B%20%20%20%20%20%20node%20%7B%20%20%20%20%20%20%20%20plotText%20%7B%20%20%20%20%20%20%20%20%20%20plainText%20%20%20%20%20%20%20%20%7D%20%20%20%20%20%20%7D%20%20%20%20%7D%20%20%7D%20%20ratingsSummary%20%7B%20%20%20%20aggregateRating%20%20%20%20voteCount%20%20%7D%20%20keywords%28first%3A%2010%29%20%7B%20%20%20%20edges%20%7B%20%20%20%20%20%20node%20%7B%20%20%20%20%20%20%20%20text%20%20%20%20%20%20%20%20interestScore%20%7B%20%20%20%20%20%20%20%20%20%20usersInterested%20%20%20%20%20%20%20%20%20%20usersVoted%20%20%20%20%20%20%20%20%7D%20%20%20%20%20%20%7D%20%20%20%20%7D%20%20%7D%7D%7D'
                url = f'https://api.graphql.imdb.com/?query={query}'
                async with ClientSession() as session:
                    print(f'getting info for {updates}')
                    res = await self.get_page(session, url, 0, headers={'content-type': 'application/json'}) #self.get_page(session, url, 0, bucket=bucket)
                    if not res:
                        print(f'error getting movie info for {urllib.parse.unquote(ttstr)}')
                        for tt in updates:
                            await self.mi_pq.put(tt)
                        continue
                    print(f'got info for {len(updates)} movies')
                    try:
                        for tt, d in zip(updates, ujson.loads(res)['data']['titles']):
                            try:
                                info = {}
                                info['tt'] = tt
                                info['title'] = d['titleText']['text'] if 'titleText' in d else ''
                                info['genres'] = [g['genre']['text'] for g in d['titleGenres']['genres'] if 'genres' in d['titleGenres']] if 'titleGenres' in d and d['titleGenres'] else []
                                info['plot'] = d['plots']['edges'][0]['node']['plotText']['plainText'] if len(d['plots']['edges']) else ''
                                info['aggregateRating'] = d['ratingsSummary']['aggregateRating'] if 'ratingsSummary' in d else -1
                                info['votingCount'] = d['ratingsSummary']['voteCount'] if 'ratingsSummary' in d else 0
                                info['keywords'] = [k['node']['text'] for k in d['keywords']['edges']]
                                await self.db.write_title(tuple(info.values()))
                                if (uids_len := len(await self.db.get_title_uids(tt))):
                                    await self.recommendations.put((int(uids_len == 1), time(), await self.db.get_title_info(tt)))
                            except Exception:
                                traceback.print_exc()
                                print(tt)
                                print(d)
                    except Exception:
                        traceback.print_exc()
            except Exception:
                traceback.print_exc()
                break

    async def all_recommendations(self): #call on start
        #finds recommendations from existing users and adds to self.recommendations. ?add with 9999999999 in place of ts so picked last
        print('running all_recommendations()')
        if time() - self.all_rec_last_ts <= 5:
            print('5 second cooldown hasn\'t elapsed')
            return
        ttl = await self.db.get_all_tids()
        [await self.mi_pq.put((0, 0, tt)) for tt in ttl]
        while not await self.db.check_ttl_exist(ttl):
            await asyncio.sleep(0.5)
        movie_info = await self.db.get_list_titles(ttl)
        for ttinfo in movie_info:
            await self.recommendations.put((int(len(await self.db.get_title_uids(ttinfo['tt'])) == 1), time()+500000000.1234, ttinfo))
        self.all_rec_last_ts = time()
        
    async def get_recommendations(self, filter_params = []): #filter with client js
        print('running get_recommendations')
        while True:
            rec = await self.recommendations.get()
            info = rec[2]
            if 'update' not in info and info['seen']:
                continue
            if len((uids := await self.db.get_title_uids(info['tt']))) >= 2:
                if 'update' in info:
                    yield [{'tt': info['tt'], 'voters': [(ur, await self.db.get_username(ur), rwinfo['rid'], rwinfo['rtitle']) for ur in await self.db.get_title_uids(info['tt']) if (rwinfo := await self.db.find_review(ur, info['tt']))]}]
                else:
                    yield [info, {'tt': info['tt'], 'voters': [(ur, await self.db.get_username(ur), rwinfo['rid'], rwinfo['rtitle']) for ur in await self.db.get_title_uids(info['tt']) if (rwinfo := await self.db.find_review(ur, info['tt']))]}]

    async def get_page(self, session, url, n, headers = {}, bucket = None):
        if not bucket:
            bucket = self.bucket
        async with bucket:
            print('getting: ' + url[:95])
            async with session.get(url, headers=headers) as res:
                if res.status != 200:
                    if res.status == 503:
                        await asyncio.sleep(30)
                    if n < 2:
                        await asyncio.sleep(5)
                        return await self.get_page(session, url, n+1, headers=headers, bucket=bucket)
                    else:
                        print(f'STATUS: {res.status} {url}')
                        return None
                return await res.text()

    async def get_user_reviews(self, ur):
        #only 8/10+. can exclude titles in movies database already (user inputted) or marked as seen
        self.fetching_urs.add(ur)
        print(f'GETTING REVIEWS FOR {ur}')
        try:
            async with ClientSession() as session:
                ####FIRST PAGE
                starts = time()
                url = f'https://www.imdb.com/user/{ur}/reviews/_ajax?sort=submissionDate&dir=desc' #newest to oldest
                res = await self.get_page(session, url, 0)
                if not res:
                    print(f'failed getting: {url}')
                    await self.db.add_user_scraped_ts(ur, starts)
                    self.fetching_urs.remove(ur)
                    return
                soup = bs(res, features='lxml')
                print('got: ' + url)
                reviews = soup.findAll('div', {'class': 'imdb-user-review'})
                #check added
                utopts = datetime.datetime.strptime(reviews[0].find('span', {'class': 'review-date'}).text, '%d %B %Y').timestamp()
                for r in reviews:
                    rid = r.attrs['data-review-id']
                    date = r.find('span', {'class': 'review-date'}).text
                    ts = datetime.datetime.strptime(date, '%d %B %Y').timestamp()
                    if ts >= await self.db.get_user_ts(ur):
                        if await self.db.check_rid_exists(rid):
                            continue
                        rtitle = r.find('a', {'class': 'title'}).text.strip()
                        rating = re.findall('([0-9]+)</span><span class="point-scale">/([0-9]+)', str(r))
                        if not len(rating):
                            continue
                        rating = rating[0]
                        tt = re.findall('a href="/title/(tt[0-9]+)/', str(r))[0]
                        await self.db.add_rid_to_users(ur, rid)
                        await self.db.write_review((tt, ur, rid, rating[0], ts, rtitle))
                        if int(rating[0]) >= 8:
                            await self.db.add_title_users(tt, ur)
                            await self.mi_pq.put((int(len(await self.db.get_title_uids(tt)) == 1), time(), tt))
                        else:
                            await self.mi_pq.put((2, time(), tt))
                    else:
                        print(f'finished getting reviews for {ur}')
                        await self.db.add_user_scraped_ts(ur, starts)
                        self.fetching_urs.remove(ur)
                        return
                try:
                    pag_key = soup.find('div', {'class': 'load-more-data'}).attrs['data-key']
                except:
                    print(f'{ur} only had 1 page of reviews')
                    await self.db.add_user_scraped_ts(ur, starts)
                    self.fetching_urs.remove(ur)
                    return
                #NEXT PAGES
                while True:
                    url = f'https://www.imdb.com/user/{ur}/reviews/_ajax?sort=submissionDate&dir=desc&paginationKey={pag_key}' #newest to oldest
                    res = await self.get_page(session, url, 0)
                    if not res:
                        print(f'failed getting: {url}')
                        self.fetching_urs.remove(ur)
                        return
                    soup = bs(res, features='lxml')
                    print('got: ' + url[:95])
                    reviews = soup.findAll('div', {'class': 'imdb-user-review'})
                    for r in reviews:
                        rid = r.attrs['data-review-id']
                        date = r.find('span', {'class': 'review-date'}).text
                        ts = datetime.datetime.strptime(date, '%d %B %Y').timestamp()
                        if ts >= await self.db.get_user_ts(ur):
                            if await self.db.check_rid_exists(rid):
                                continue
                            rtitle = r.find('a', {'class': 'title'}).text.strip()
                            rating = re.findall('([0-9]+)</span><span class="point-scale">/([0-9]+)', str(r))
                            if not len(rating):
                                continue
                            rating = rating[0]
                            tt = re.findall('a href="/title/(tt[0-9]+)/', str(r))[0]
                            #add to movie info priority queue to update movie_info, movies
                            await self.db.add_rid_to_users(ur, rid)
                            await self.db.write_review([tt, ur, rid, rating[0], ts, rtitle])
                            if int(rating[0]) >= 8:
                                await self.db.add_title_users(tt, ur)
                                await self.mi_pq.put((int(len(await self.db.get_title_uids(tt)) == 1), time(), tt))
                            else:
                                await self.mi_pq.put((2, time(), tt))
                        else:
                            print(f'finished getting reviews for {ur}')
                            await self.db.add_user_scraped_ts(ur, starts)
                            self.fetching_urs.remove(ur)
                            return
                    try:
                        pag_key = soup.find('div', {'class': 'load-more-data'}).attrs['data-key']
                    except:
                        print(f'finished getting reviews for {ur}')
                        await self.db.add_user_scraped_ts(ur, starts)
                        self.fetching_urs.remove(ur)
                        return
        except Exception:
            traceback.print_exc()
            self.fetching_urs.remove(ur)

    async def find_recommendations(self):
        #run on startup with users from added db and add new on 'add' button click
        print('running find_recommendations')
        try:
            while True:
                #CHECK/UPDATE UTOP FOR LAST SCRAPED TS
                urfetch = [ur for ur in await self.db.get_all_added_uids() if ur not in self.fetching_urs and (time() - await self.db.get_user_ts(ur)) >= 12*60*60] #at most scrapes user reviews every 12 hours
                if not len(urfetch):
                    await asyncio.sleep(60)
                    continue
                print(f'scraping reviews from: {urfetch}')
                #start n tasks list
                n = min(10, len(urfetch))
                tasks = []
                for ur in list(urfetch[:n]):
                    task = asyncio.create_task(self.get_user_reviews(ur))
                    tasks.append(task)
                    task.add_done_callback(tasks.remove)
                    urfetch.remove(ur)
                while len(tasks):
                    if len(tasks) < n:
                        urfetch = [ur for ur in await self.db.get_all_added_uids() if ur not in self.fetching_urs and (time() - await self.db.get_user_ts(ur)) >= 12*60*60]
                        print(f'new urfetch: {urfetch}')
                        n = min(10, len(urfetch))
                        for ur in list(urfetch[:n-len(tasks)]):
                            task = asyncio.create_task(self.get_user_reviews(ur))
                            tasks.append(task)
                            task.add_done_callback(tasks.remove)
                            urfetch.remove(ur)
                    await asyncio.sleep(20) #could set to wait until a task finishes
        except Exception:
            traceback.print_exc()

    async def fetch_reviews(self, tt, q):
        async with ClientSession() as session:
            try:
                url = f'https://www.imdb.com/title/{tt}/reviews/_ajax?sort=submissionDate&dir=desc&ratingFilter=0'
                print('getting reviews for: ' + url)
                res = await self.get_page(session, url, 0)
                if not res:
                    print(tt)
                    print(self.errs[0])
                    await q.put([tt, 0])
                    return
                soup = bs(res, features='lxml')
                print('got: ' + url)
                reviews = soup.findAll('div', {'class': 'imdb-user-review'})
                c = 0
                await self.mi_pq.put((0, time(), tt))
                while not (await self.db.check_ttl_exist([tt])):
                    await asyncio.sleep(0.5)
                topts = datetime.datetime.strptime(reviews[0].find('span', {'class': 'review-date'}).text, '%d %B %Y').timestamp()
                for r in reviews:
                    rating = re.findall('([0-9]+)</span><span class="point-scale">/([0-9]+)', str(r))
                    if not len(rating):
                        continue
                    c += 1
                    rating = rating[0]
                    user = list(r.find('span', {'class': 'display-name-link'}).children)[0].text
                    uid = re.findall('a href="/user/(ur[0-9]+)/', str(r))[0]
                    await self.db.add_username(uid, user)
                    rid = r.attrs['data-review-id']
                    date = r.find('span', {'class': 'review-date'}).text
                    ts = datetime.datetime.strptime(date, '%d %B %Y').timestamp()
                    rtitle = r.find('a', {'class': 'title'}).text.strip()
                    #if tt in database:
                    if ts >= (await self.db.get_title_info_attr(tt, ('most_recent_review_ts', )))['most_recent_review_ts']: # and not (await self.db.check_rid_exists(rid)): - can't be checked to allow rescraping reviews by setting "most_recently_review_ts" in titles table to 0
                        await self.db.add_rid_to_users(uid, rid)
                        await self.db.write_review([tt, uid, rid, rating[0], ts, rtitle])
                        rcount = len(await self.db.get_user_rids_ttl(uid, self.ttlist))
                        if rcount >= self.min_reviews:
                            mtitle = (await self.db.get_title_info_attr(tt, ('title', )))['title']
                            await q.put(f'UUT|{tt}|{mtitle}|{uid}|{user}|{rid}|{"/".join(rating)}|{await self.db.check_user_added(uid)}')
                    else:
                        rcount = len(await self.db.get_user_rids_ttl(uid, self.ttlist))
                        if rcount >= self.min_reviews:
                            mtitle = (await self.db.get_title_info_attr(tt, ('title', )))['title']
                            await q.put(f'UUT|{tt}|{mtitle}|{uid}|{user}|{rid}|{"/".join(rating)}|{await self.db.check_user_added(uid)}')
                        await self.db.set_title_info_attr(tt, ('most_recent_review_ts', ), (topts, ))
                        return
                total = c
                await q.put(f'{tt},{total}')
                try:
                    pag_key = soup.find('div', {'class': 'load-more-data'}).attrs['data-key']
                except:
                    await q.put([tt, 2])
                    await self.db.set_title_info_attr(tt, ('most_recent_review_ts', ), (topts, ))
                    return
                while True:
                    url = f'https://www.imdb.com/title/{tt}/reviews/_ajax?sort=submissionDate&dir=desc&ratingFilter=0&paginationKey={pag_key}'
                    res = await self.get_page(session, url, 0)
                    if not res:
                        print(tt)
                        print(self.errs[1])
                        await q.put([tt, 1])
                        await self.db.set_title_info_attr(tt, ('most_recent_review_ts', ), (topts, ))
                        return
                    soup = bs(res, features='lxml')
                    reviews = soup.findAll('div', {'class': 'imdb-user-review'})
                    c = 0
                    for r in reviews:
                        rating = re.findall('([0-9]+)</span><span class="point-scale">/([0-9]+)', str(r))
                        if not len(rating):
                            continue
                        c += 1
                        rating = rating[0]
                        user = list(r.find('span', {'class': 'display-name-link'}).children)[0].text
                        uid = re.findall('a href="/user/(ur[0-9]+)/', str(r))[0]
                        await self.db.add_username(uid, user)
                        rid = r.attrs['data-review-id']
                        date = r.find('span', {'class': 'review-date'}).text
                        ts = datetime.datetime.strptime(date, '%d %B %Y').timestamp()
                        rtitle = r.find('a', {'class': 'title'}).text.strip()
                        if ts >= (await self.db.get_title_info_attr(tt, ('most_recent_review_ts', )))['most_recent_review_ts']: # and not await self.db.check_rid_exists(rid): #if ts >= top.ts and not await self.db.fetch_one(f'SELECT * FROM reviews WHERE id = "{uid}"'): #self.dbs.query(Review).where(Review.id == uid).first():
                            await self.db.add_rid_to_users(uid, rid)
                            await self.db.write_review([tt, uid, rid, rating[0], ts, rtitle])
                            rcount = len(await self.db.get_user_rids_ttl(uid, self.ttlist))
                            if rcount >= self.min_reviews:
                                mtitle = (await self.db.get_title_info_attr(tt, ('title', )))['title']
                                await q.put(f'UUT|{tt}|{mtitle}|{uid}|{user}|{rid}|{"/".join(rating)}|{await self.db.check_user_added(uid)}')
                        else:
                            rcount = len(await self.db.get_user_rids_ttl(uid, self.ttlist))
                            if rcount >= self.min_reviews:
                                mtitle = (await self.db.get_title_info_attr(tt, ('title', )))['title']
                                await q.put(f'UUT|{tt}|{mtitle}|{uid}|{user}|{rid}|{"/".join(rating)}|{await self.db.check_user_added(uid)}')
                            await self.db.set_title_info_attr(tt, ('most_recent_review_ts', ), (topts, ))
                            return
                    total += c
                    await q.put(f'{tt},{total}')
                    try:
                        pag_key = soup.find('div', {'class': 'load-more-data'}).attrs['data-key']
                    except:
                        await q.put([tt, 2])
                        await self.db.set_title_info_attr(tt, ('most_recent_review_ts', ), (topts, ))
                        return
            except Exception:
                traceback.print_exc()

    async def get_reviews(self, ttl):
        self.min_reviews = max(2, math.ceil(len(ttl)/2)) #minimum reviews of movies from list user must have to be displayed
        q = asyncio.Queue()
        tasks = []
        self.ttlist = ttl
        self.get_updates = True
        for tt in ttl:
            task = asyncio.create_task(self.fetch_reviews(tt, q))
            tasks.append(task)
            task.add_done_callback(tasks.remove)
        print('created tasks')
        while len(tasks):
            try:
                msg = await asyncio.wait_for(q.get(), timeout=5, loop=self.loop)
            except asyncio.TimeoutError:
                continue
            print('got msg: ' + str(msg))
            yield msg
        self.get_updates = False
        print('finished getting reviews')
        for tt in ttl:
            yield f'{tt},DONE'
        self.get_updates = True
        for uid in await self.db.get_all_uids():
            urids = await self.db.get_user_rids_ttl(uid, ttl)
            if len(urids) >= self.min_reviews: #only check for # of reviews for provided ttl
                for rid in urids:
                    r = await self.db.get_review(rid)
                    mtitle = (await self.db.get_title_info_attr(r['tt'], ('title', )))['title']
                    uname = await self.db.get_username(uid)
                    yield f'UUT|{r["tt"]}|{mtitle}|{uid}|{uname}|{r["rid"]}|{r["rating"]}/10|{await self.db.check_user_added(uid)}'

    async def get_info(self, session, tt, n):
        url = f'https://www.imdb.com/title/{tt}/reviews'
        async with self.bucket:
            async with session.get(url) as res:
                if res.status == 503:
                    await asyncio.sleep(30)
                if res.status != 200:
                    if n < 2:
                        await asyncio.sleep(5)
                        return await self.get_info(session, tt, n+1)
                    else:
                        return tt
                html = bs(await res.text(), features='lxml')
                el = html.find('div', {'class': 'subpage_title_block__right-column'})
                title = el.find('a').text #name
                title += ' ' + el.find('span').text.strip() #year
                nreviews = '~' + html.find('div', {'class': 'header'}).find('span').text
                await self.db.set_title_info_attr(tt, ['title'], [title])
                return [tt, title, nreviews]

    async def get_infos(self, ttl):
        await self.db.bulk_set_seen(ttl)
        async with ClientSession() as session:
            tasks = [self.get_info(session, tt, 0) for tt in ttl]
            for res in asyncio.as_completed(tasks):
                yield await res
