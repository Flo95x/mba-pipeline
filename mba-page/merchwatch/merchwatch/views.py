from django.http import HttpResponse
from django.shortcuts import render 
from django.template import loader
from django import template
import pandas as pd
from google.cloud import bigquery
import itertools
from sklearn import preprocessing
import os 
from datetime import date
import datetime 
import time 

register = template.Library()

#def homepage(request):
    #return HttpResponse("Ich geh dir fremd :O")
#    return render(request, "main.html")

def about(request):
    return HttpResponse("about")


def get_sql(marketplace, limit, filter=None):
    if limit == None:
        SQL_LIMIT = ""
    elif type(limit) == int and limit > 0:
        SQL_LIMIT = "LIMIT " + str(limit)
    else:
        assert False, "limit is not correctly set"

    
    if filter == None:
        SQL_WHERE= "where bsr != 0 and bsr != 404"
    elif filter == "only 404":
        SQL_WHERE = "where bsr = 404"
    elif filter == "only 0":
        SQL_WHERE = "where bsr = 0"
    else:
        assert False, "filter is not correctly set"

    SQL_STATEMENT = """
    SELECT t_fin.* FROM (
SELECT t0.*, t2.title, DATE_DIFF(current_date(), Date(t2.upload_date), DAY) as time_since_upload,Date(t2.upload_date) as upload_date, t2.product_features, t1.url FROM (
    SELECT asin, AVG(price) as price_mean,MAX(price) as price_max,MIN(price) as price_min,
            AVG(bsr) as bsr_mean, MAX(bsr) as bsr_max,MIN(bsr) as bsr_min, COUNT(*) as bsr_count,
            AVG(customer_review_score_mean) as score_mean, MAX(customer_review_score_mean) as score_max, MIN(customer_review_score_mean) as score_min 
            FROM `mba-pipeline.mba_{0}.products_details_daily`
    where bsr != 0 and bsr != 404
    group by asin
    ) t0
    left join `mba-pipeline.mba_de.products_images` t1 on t0.asin = t1.asin
    left join `mba-pipeline.mba_de.products_details` t2 on t0.asin = t2.asin
  
    
    union all 
    
    SELECT t0.*, t2.title, DATE_DIFF(current_date(), Date(t2.upload_date), DAY) as time_since_upload,Date(t2.upload_date) as upload_date, t2.product_features, t1.url FROM (
    SELECT asin, AVG(price) as price_mean,MAX(price) as price_max,MIN(price) as price_min,
            AVG(bsr) as bsr_mean, MAX(bsr) as bsr_max,MIN(bsr) as bsr_min, COUNT(*) as bsr_count,
            AVG(customer_review_score_mean) as score_mean, MAX(customer_review_score_mean) as score_max, MIN(customer_review_score_mean) as score_min 
            FROM `mba-pipeline.mba_{0}.products_details_daily`
    where bsr = 0 and bsr != 404
    and asin NOT IN (SELECT asin FROM `mba-pipeline.mba_de.products_details_daily` WHERE bsr != 0 and bsr != 404 group by asin)
    group by asin
    ) t0
    left join `mba-pipeline.mba_de.products_images` t1 on t0.asin = t1.asin
    left join `mba-pipeline.mba_de.products_details` t2 on t0.asin = t2.asin
    
    ) t_fin
    order by t_fin.bsr_mean
    {1}
    """.format(marketplace, SQL_LIMIT)
    return SQL_STATEMENT

def make_trend_column(df_shirts):
    df_shirts = df_shirts.reset_index()
    x = df_shirts[["time_since_upload"]].values 
    min_max_scaler = preprocessing.MinMaxScaler()
    x_scaled = min_max_scaler.fit_transform(x)
    df = pd.DataFrame(x_scaled)
    df_shirts["time_since_upload_norm"] = df.iloc[:,0]
    df_shirts["trend"] = df_shirts["bsr_mean"] * df_shirts["time_since_upload_norm"] * 2
    return df_shirts

def check_if_shirts_today_exist(file_path):
    date_creation = time.ctime(os.path.getctime(file_path))
    return date.today() == datetime.datetime.strptime(date_creation, "%a %b %d %H:%M:%S %Y").date()

def get_shirts(marketplace, limit=None, in_test_mode=False, filter=None):
    print(os.getcwd())
    file_path = "merchwatch/data/shirts.csv"
    if check_if_shirts_today_exist(file_path):
        print("Data already loaded today")
        df_shirts=pd.read_csv("merchwatch/data/shirts.csv", sep="\t")
    else:
        print("Load shirt data from bigquery")
        project_id = 'mba-pipeline'
        bq_client = bigquery.Client(project=project_id)
        df_shirts = bq_client.query(get_sql(marketplace, limit, filter)).to_dataframe().drop_duplicates()
        df_shirts = make_trend_column(df_shirts)
        df_shirts.to_csv("merchwatch/data/shirts.csv", index=None, sep="\t")
        print("Loading completed.")
        #df_shirts[df_shirts["bsr_mean"] != 0][["trend", "time_since_upload","time_since_upload_norm", "bsr_mean"]].head(10)
    
    return df_shirts

    '''
    if in_test_mode:
        df_shirts=pd.read_csv("merchwatch/data/shirts2.csv", sep="\t")
    else:
        project_id = 'mba-pipeline'
        bq_client = bigquery.Client(project=project_id)
        df_shirts = bq_client.query(get_sql(marketplace, limit, filter)).to_dataframe().drop_duplicates()
    '''
    
def main(request):
    iterator=itertools.count()
    marketplace = "de"
    
    sort_by = request.GET.get('sort_by')
    desc = request.GET.get('direction')
    info = request.GET.get('info')
    filter = request.GET.get('filter')
    columns = request.GET.get('columns')
    rows = request.GET.get('rows')
    key = request.GET.get('s')

    if filter == "0":
        filter = "only 0"
    elif filter == "404":
        filter = "only 404"
    #q_desc = request.GET["direction"]

    df_shirts = get_shirts(marketplace, limit=None, in_test_mode=True, filter=filter)
    df_shirts = df_shirts.round(2)

    if key != None:
        df_shirts = df_shirts[df_shirts.apply(lambda x: key.lower() in x.product_features.lower() or key.lower() in x.title.lower(), axis=1)]
        #df_shirts  = df_shirts[df_shirts["product_features"].str.contains(key, case=False)]

    if sort_by != None:
        if desc == "desc":
            if "bsr" in sort_by or "trend" in sort_by: 
                df_shirts = df_shirts[df_shirts["bsr_max"]!=0].sort_values(sort_by, ascending=False)
            else:
                df_shirts = df_shirts.sort_values(sort_by, ascending=False)
        else:
            if "bsr" in sort_by or "trend" in sort_by: 
                df_shirts = df_shirts[df_shirts["bsr_max"]!=0].sort_values(sort_by, ascending=True)
            else:
                df_shirts = df_shirts.sort_values(sort_by, ascending=True)

    number_shirts = len(df_shirts)
    if columns == None:
        columns = 6
    else:
        columns = int(columns)
    row_max = int(number_shirts / columns)

    if rows == None:
        rows = 5
    else:
        rows = int(rows)
    if rows > row_max:
        rows = row_max
        
    shirt_info = df_shirts.to_dict(orient='list')
    #context = {"asin": ["awdwa","awdwawdd", "2312313"],}
    return render(request, 'main_boss.html', {"shirt_info":shirt_info, "iterator":iterator, "columns" : columns, "rows": rows,"show_detail_info":info, "sort_by":sort_by})
    #return HttpResponse(template.render(context, request))

#df_shirts = get_shirts("de", limit=None, in_test_mode=False)
#df_shirts.to_csv("mba-pipeline/mba-page/merchwatch/merchwatch/data/shirts2.csv", index=None, sep="\t")
#test = 0