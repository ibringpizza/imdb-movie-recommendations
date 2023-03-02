# imdb-movie-recommendations

### Screenshots

![Pasted image 20230301122042](https://user-images.githubusercontent.com/52234395/222295548-243661d7-7212-4f8c-95b2-c471be17f1e2.png)

![Pasted image 20230301113616](https://user-images.githubusercontent.com/52234395/222295517-6023264a-1d8c-4fe5-a84c-e8ea1e73c601.png)

### Find Recommendations
- Add IMDB title ids (ex: tt0062622) to the text area on the top of the page. One title id per line. There should be at least two titles so that users who've reviewed both can be compared. Users who've only reviewed one of the movies you add here will not be displayed. More information in Technical Details.
- Add users you want to get recommendations from by clicking the 'add' button next to their username.
- The bot requires at least 2 different added users to have reviewed a title to display it as a recommendation. You can change this number in `get_movie_info()` and `get_recommendations()`.

### Setup
- A database needs to be created to store all of the data collected by the bot.

- ##### Creating the database
	- On Linux:

		Install PSQL
		```
		sudo apt install postgresql-client-common
		```
		You may have to start the postgresql server after installing on your particular distro. Installing with Apt should automatically start the server on the default port 5432.

		Create user. May display error
		```
		sudo -u postgres createuser dev
		```

		Login to postgres as superuser
		```
		sudo -u postgres psql
		```

		Verify user 'dev' was created by listing all users
		```
		postgres=# \du+
		```

		Change user password
		```
		postgres=# alter user dev with password 'dev';
		```

		Exit shell with ctrl+d or '\\q'

		Create new database. May display error
		```
		sudo -u postgres createdb -O dev imdb
		```

		Login to verify database was created and start creating tables
		```
		psql -h localhost -p 5432 -U dev imdb
		```
		If you'd like to use a different database name, username, and password, you need to edit the psycopg database connection in app.py

		##### Create the tables

		```
		CREATE TABLE reviews (
			rid varchar(11) PRIMARY KEY,
			uid varchar(11),
			rating integer,
			ts bigint,
			rtitle varchar,
			tt varchar(11)
		);
		```

		```
		CREATE TABLE titles (
			tt varchar(11) PRIMARY KEY,
			title varchar,
			genres varchar[],
			plot varchar,
			aggregaterating real,
			votingcount integer,
			keywords varchar[],
			seen boolean DEFAULT false,
			most_recent_review_ts bigint DEFAULT 0
		);
		```

		```
		CREATE TABLE users (
			uid varchar(11) PRIMARY KEY,
			rids varchar[] DEFAULT '{}',
			username varchar,
			last_scraped_ts bigint,
			added boolean DEFAULT false
		);
		```

		```
		CREATE TABLE title_users (
			tt varchar(11) PRIMARY KEY,
			uids varchar[]
		);
		```
- #### Finally, to run:
	- Install dependencies:
		```
		python3 -m pip install -r requirements.txt
		```
	- Run app:
		```
		python3 app.py
		```

### Important
- Try not to kill the bot while it is gathering reviews for a new title or user. It will not be able to resume scraping without manual assistance. More information in Technical Details.
- This bot is **only** designed to be run on your local network. It can manage multiple sessions but can slow down.
- The ratings table generated from your title IDs is not saved with your session and will clear when the page is refreshed.

### Technical Details
- The list of previously specified titles is held and resubmitted with any new titles to keep all data up-to-date. The backend doesn't discriminate between previously specified titles and new titles and will send all user review data again. This can be done more efficiently.
- **To be displayed in the ratings table**, users need to have (public) reviews of at least half of the titles specified. This line sets the number of minimum reviews:
	- `self.min_reviews = max(2, math.ceil(len(ttl)/2))` (get_reviews(), get_reviews.py)
- #### Process
	- Newly added title IDs are sent over the websocket, appended to whatever previous tts were received, and passed to `get_reviews()` in *get_reviews.py*. The `ratings` table is built as reviews are collected and users who meet the minimum reviews requirement are added with their username and ratings.
- #### Scraping
	- ##### Responsible Web Scraping
		- While the program is asynchronous, a leaky bucket algorithm is used to keep all requests to IMDB at least 2 seconds apart.
		- All data required to run the bot is saved locally and minimal subsequent requests are required to collect new reviews.
	- Currently, the bot only scrapes from a user's reviews page, not their ratings page. Only written reviews are scraped.
	- Reviews from added users are refreshed every 12 hours.
- #### What data is saved locally?
	- For movies:
		- Title ID, title, plot, aggregate rating, # of ratings, the top 15 keywords
	- For user reviews:
		- Review title, timestamp, user ID, rating, title ID
	- For user profiles:
		- User ID, username
		- If the user is added to your watchlist, all of their written reviews are saved, review content excluded.

### Bugs
- Users with only 1 apparent review are sometimes displayed in the reviews table after adding titles. Users can review a film multiple times and the check used to make sure they meet the `min_reviews` requirement doesn't differentiate between multiple reviews for the same movie or several movies. A fix could be a database query to the `title_users` table instead of the `users` table.

### Future Fixes
- Several database queries could be made more efficient.
- The review count update in the progress table doesn't display as DONE until the last title's reviews are collected.
- The ratings table only sorts users from most reviews to least after the bot is done scraping.
- The backend doesn't discriminate between previously specified titles and new titles and will send all user review data again. This can be done more efficiently.
