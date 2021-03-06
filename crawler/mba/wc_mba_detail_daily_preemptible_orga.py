import subprocess
import os
import argparse
import time 
import sys
import numpy as np 
import pandas as pd
from google.cloud import bigquery
import datetime
import utils


def get_asin_product_detail_to_crawl(marketplace, daily):
    project_id = 'mba-pipeline'
    reservationdate = datetime.datetime.now()
    #reservationdate = datetime.date(2020, 6, 6)
    dataset_id = "preemptible_logs"
    bq_client = bigquery.Client(project=project_id)
    if daily:
        table_id = "mba_detail_daily_" + marketplace + "_preemptible_%s_%s_%s"%(reservationdate.year, reservationdate.month, reservationdate.day)
        df_product = bq_client.query("SELECT t0.asin, t0.url_product FROM mba_" + marketplace + ".products t0 LEFT JOIN (SELECT * FROM mba_" + marketplace + ".products_details_daily WHERE DATE(timestamp) = '%s-%s-%s' or price_str = '404') t1 on t0.asin = t1.asin where t1.asin IS NULL order by t0.timestamp" %(reservationdate.year, reservationdate.month, reservationdate.day)).to_dataframe().drop_duplicates()
    else:
        table_id = "mba_detail_" + marketplace + "_preemptible_%s_%s_%s"%(reservationdate.year, reservationdate.month, reservationdate.day)
        df_product = bq_client.query("SELECT t0.asin, t0.url_product FROM mba_" + marketplace + ".products t0 LEFT JOIN mba_" + marketplace + ".products_details t1 on t0.asin = t1.asin where t1.asin IS NULL order by t0.timestamp").to_dataframe().drop_duplicates()
    
    reservation_table_id = dataset_id + "." + table_id
    if utils.does_table_exist(project_id, dataset_id, table_id):
            # get reservation logs
            df_reservation = bq_client.query("SELECT * FROM " + reservation_table_id + " t0 order by t0.timestamp DESC").to_dataframe().drop_duplicates()
            df_reservation_status = df_reservation.drop_duplicates("asin")
            # get list of asins that are currently blocked by preemptible instances
            asins_blocked = df_reservation_status[df_reservation_status["status"] == "blocked"]["asin"].tolist()
            # filter asins for those which are not blocked
            matching_asins = df_product["asin"].isin(asins_blocked)
            print("%s asins are currently blocked and will not be crawled" % str(len([i for i in matching_asins if i == True])))
            df_product = df_product[~matching_asins]

    return df_product

def get_blacklisted_ips(marketplace, daily):
    project_id = 'mba-pipeline'
    reservationdate = datetime.datetime.now()
    dataset_id = "preemptible_logs"
    bq_client = bigquery.Client(project=project_id)
    if daily:
        table_id = "mba_detail_daily_" + marketplace + "_preemptible_%s_%s_%s"%(reservationdate.year, reservationdate.month, reservationdate.day)
    else:
        table_id = "mba_detail_" + marketplace + "_preemptible_%s_%s_%s"%(reservationdate.year, reservationdate.month, reservationdate.day)
    
    reservation_table_id = dataset_id + "." + table_id
    ip_blocked = []
    if utils.does_table_exist(project_id, dataset_id, table_id):
        # get reservation logs
        df_reservation = bq_client.query("SELECT * FROM " + reservation_table_id + " t0 WHERE status = 'blacklist' order by t0.timestamp DESC").to_dataframe().drop_duplicates()
        df_reservation_status = df_reservation.drop_duplicates("ip_address")
        # get list of asins that are currently blocked by preemptible instances
        ip_blocked = df_reservation_status["ip_address"].tolist()
    return ip_blocked

def create_startup_script(marketplace, number_products, connection_timeout, time_break_sec, seconds_between_crawl, preemptible_code, pre_instance_name, zone, daily, api_key, chat_id):
    if daily:
        py_script = "wc_mba_detail_daily.py"
    else:
        py_script = "wc_mba_detail.py"
    startup_script = '''#!/bin/sh
cd home/
sudo rm -rf mba-pipeline
sudo git clone https://github.com/Flo95x/mba-pipeline.git
pip3 install -r /home/mba-pipeline/crawler/mba/requirements.txt 
cd mba-pipeline/crawler/mba/
sudo mkdir data
sudo chmod 777 data/
/usr/bin/python3 /home/mba-pipeline/crawler/mba/{} {} --telegram_api_key {} --telegram_chatid {} --number_products {} --connection_timeout {} --time_break_sec {} --seconds_between_crawl {} --preemptible_code {} --pre_instance_name {} --zone {}
    '''.format(py_script, marketplace, api_key, chat_id, number_products, connection_timeout, time_break_sec, seconds_between_crawl, preemptible_code, pre_instance_name, zone)
    # save product detail page locally
    with open("pre_startup_script.sh", "w+") as f:
        f.write(startup_script)

def get_bash_create_pre_instance(instance_name, zone):
    bash_command = 'gcloud compute instances create {} --machine-type=f1-micro --preemptible --zone {}  --labels crawler-auto=crawler-auto-instance-pre --service-account mba-admin@mba-pipeline.iam.gserviceaccount.com --image-project mba-pipeline --image crawler-amazon-us --metadata-from-file startup-script=pre_startup_script.sh --scopes storage-full,cloud-platform,bigquery'.format(instance_name, zone)
    return bash_command

def get_bash_start_pre_instance(instance_name, zone):
    bash_command = 'gcloud compute instances start {} --zone {}  '.format(instance_name, zone)
    return bash_command

def get_bash_describe_pre_instance(instance_name, zone):
    bash_command = 'gcloud compute instances describe {} --zone {}  '.format(instance_name, zone)
    return bash_command

def get_bash_delete_pre_instance(instance_name, zone):
    bash_command = 'yes Y | gcloud compute instances delete {} --zone {}  '.format(instance_name, zone)
    return bash_command

def get_bash_stop_instance(instance_name, zone):
    bash_command = 'yes Y | gcloud compute instances stop {} --zone {}  '.format(instance_name, zone)
    return bash_command

def get_currently_running_instance(number_running_instances, marketplace, max_instances_of_zone, region_space):
    currently_running_instance = []
    for i in range(number_running_instances):
        zone = utils.get_zone_of_marketplace(marketplace, max_instances_of_zone=max_instances_of_zone, number_running_instances=i,region_space=region_space)
        pre_instance_name = "mba-"+marketplace+"-detail-pre-"+ str(i+1)
        bashCommand = get_bash_describe_pre_instance(pre_instance_name,zone)
        process = subprocess.Popen(bashCommand.split(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False)
        output, error = process.communicate()
        try:
            status = str(output).split("status: ")[1].split("\\")[0]
        except:
            status = "NOT EXISTEND"
        if status.upper() == "RUNNING":
            currently_running_instance.append(pre_instance_name)

    return currently_running_instance

def get_currently_terminated_instance(number_running_instances, marketplace, max_instances_of_zone,region_space):
    currently_terminated_instance = []
    for i in range(number_running_instances):
        zone = utils.get_zone_of_marketplace(marketplace, max_instances_of_zone=max_instances_of_zone, number_running_instances=i,region_space=region_space)
        pre_instance_name = "mba-"+marketplace+"-detail-pre-"+ str(i+1)
        bashCommand = get_bash_describe_pre_instance(pre_instance_name,zone)
        process = subprocess.Popen(bashCommand.split(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False)
        output, error = process.communicate()
        try:
            status = str(output).split("status: ")[1].split("\\")[0]
        except:
            status = "NOT EXISTEND"
        if status.upper() == "TERMINATED":
            currently_terminated_instance.append(pre_instance_name)

    return currently_terminated_instance

def update_preemptible_logs(pree_id, marketplace, status, is_daily):
    project_id = 'mba-pipeline'
    timestamp = datetime.datetime.now()
    dataset_id = "preemptible_logs"
    if is_daily:
        table_id = "mba_detail_daily_" + marketplace + "_preemptible_%s_%s_%s"%(timestamp.year, timestamp.month, timestamp.day)
    else:
        table_id = "mba_detail_" + marketplace + "_preemptible_%s_%s_%s"%(timestamp.year, timestamp.month, timestamp.day)
    reservation_table_id = dataset_id + "." + table_id
    bq_client = bigquery.Client(project=project_id)
    # get reservation logs
    df_reservation = bq_client.query("SELECT * FROM " + reservation_table_id + " t0 WHERE t0.pree_id = '{}' order by t0.timestamp DESC".format(pree_id)).to_dataframe().drop_duplicates()
    df_reservation_status = df_reservation.drop_duplicates("asin")
    # get list of asins that are currently blocked by preemptible instances
    df_reservation_status_blocked = df_reservation_status[df_reservation_status["status"] == "blocked"]
    print("%s asins were not correctly crawled by %s" %(len(df_reservation_status_blocked), pree_id))
    df_reservation_status_blocked['timestamp'] = timestamp
    df_reservation_status_blocked['timestamp'] = df_reservation_status_blocked['timestamp'].astype('datetime64')
    df_reservation_status_blocked['status'] = status
    if len(df_reservation_status_blocked) > 0 and is_daily:
        try:
            df_reservation_status_blocked.to_gbq("preemptible_logs.mba_detail_daily_" + marketplace + "_preemptible_%s_%s_%s"%(timestamp.year, timestamp.month, timestamp.day),project_id="mba-pipeline", if_exists="append")
        except:
            pass
    elif len(df_reservation_status_blocked) > 0 and not is_daily:
        try:
            df_reservation_status_blocked.to_gbq("preemptible_logs.mba_detail_" + marketplace + "_preemptible_%s_%s_%s"%(timestamp.year, timestamp.month, timestamp.day),project_id="mba-pipeline", if_exists="append")
        except:
            pass

def start_instance(marketplace, number_running_instances, number_products,connection_timeout, time_break_sec, seconds_between_crawl, pree_id, id, zone, max_instances_of_zone, daily, api_key, chat_id, blocked_ips,region_space, time_start, instance_name, stop_instance_by_itself):
    pre_instance_name = "mba-"+marketplace+"-detail-pre-"+ str(id)
    create_startup_script(marketplace, number_products, connection_timeout, time_break_sec, seconds_between_crawl, pree_id, pre_instance_name, zone, daily, api_key, chat_id)
    # get terminated instances
    currently_terminated_instance = get_currently_terminated_instance(number_running_instances, marketplace, max_instances_of_zone,region_space)
    # if instance is terminated it should be restarted and not recreated
    if pre_instance_name in currently_terminated_instance:
        bashCommand = get_bash_start_pre_instance(pre_instance_name,zone)
    # if instance does not exists it should be created
    else:
        bashCommand = get_bash_create_pre_instance(pre_instance_name,zone)
    process = subprocess.Popen(bashCommand.split(), stdout=subprocess.PIPE)
    output, error = process.communicate()
    ip_address = utils.get_extrenal_ip(pre_instance_name, zone)
    time_start = time.time()
    while ip_address in blocked_ips:
        # delete instance
        print("IP address %s is blocked. Instance will be deleted again..." % ip_address)
        bashCommand_delete = get_bash_delete_pre_instance(pre_instance_name, zone)
        stream = os.popen(bashCommand_delete)
        output = stream.read()
        # create instance
        bashCommand = get_bash_create_pre_instance(pre_instance_name,zone)
        process = subprocess.Popen(bashCommand.split(), stdout=subprocess.PIPE)
        output, error = process.communicate()
        ip_address = utils.get_extrenal_ip(pre_instance_name, zone)
        # cant find instance in five minutes -> break
        if(time.time()-time_start) > 60*5:
            finish_script(5,seconds_between_crawl,number_products,0,number_running_instances,marketplace,max_instances_of_zone,region_space,time_start, api_key, chat_id)
            print("No ip address left")
            if stop_instance_by_itself:
                bashCommand = get_bash_stop_instance(instance_name, "us-west1-b")
                stream = os.popen(bashCommand)
                output = stream.read()
            assert False, "No ip address left"

def delete_all_instance(number_running_instances, marketplace, max_instances_of_zone,region_space):
    print("Start to delete all preemptible instances")
    for i in range(number_running_instances):
        zone = utils.get_zone_of_marketplace(marketplace, max_instances_of_zone=max_instances_of_zone, number_running_instances=i,region_space=region_space)
        pre_instance_name = "mba-"+marketplace+"-detail-pre-"+ str(i+1)
        bashCommand = get_bash_delete_pre_instance(pre_instance_name, zone)
        stream = os.popen(bashCommand)
        output = stream.read()

def finish_script(puffer_minutes,seconds_between_crawl,number_products,time_wait_minutes,number_running_instances,marketplace, max_instances_of_zone,region_space,time_start, api_key, chat_id):
    time_sleep_minutes = ((seconds_between_crawl * number_products) / 60) - time_wait_minutes + puffer_minutes
    telegram_text = "Crawling is finished. Wait %s minutes to make sure that all scripts are finished" % (time_sleep_minutes)
    utils.send_msg(chat_id, telegram_text, api_key)
    print("Crawling is finished. Wait %s minutes to make sure that all scripts are finished." % time_sleep_minutes)
    time.sleep(time_sleep_minutes*60)
    delete_all_instance(number_running_instances, marketplace, max_instances_of_zone,region_space=region_space)
    print("Elapsed time: %.2f minutes" % ((time.time() - time_start)/60))
    
def main(argv):
    parser = argparse.ArgumentParser(description='')
    parser.add_argument('marketplace', help='Shortcut of mba marketplace. I.e "com" or "de", "uk"', type=str)
    parser.add_argument('daily', type=utils.str2bool, nargs='?', const=True, help='Should the webcrawler for daily crawling be used or the normal one time detail crawler?')
    parser.add_argument('region_space', type=int, help='1 = Zürich and Frankfurt, 2 = London and Irland, 3 = Belgien and Niederlande ')
    parser.add_argument('--telegram_api_key',default="", help='API key of mba bot', type=str)
    parser.add_argument('--telegram_chatid', default="", help='Id of channel like private chat or group channel', type=str)
    parser.add_argument('--number_running_instances', default=3, type=int, help='Number of preemptible instances that shoul run parallel. Default is 3.')
    parser.add_argument('--number_products', default=10, type=int, help='Number of products/shirts that shoul be crawled per instance. If 0, every shirt that is not already crawled will be crawled.')
    parser.add_argument('--number_products_total', default=0, type=int, help='Number of products/shirts that shoul be crawled in total. If 0, every shirt that is not already crawled will be crawled.')
    parser.add_argument('--connection_timeout', default=10.0, type=float, help='Time that the request operation has until its breaks up. Default: 10.0 sec')
    parser.add_argument('--time_break_sec', default=240, type=int, help='Time in seconds the script tries to get response of certain product. Default 240 sec')
    parser.add_argument('--seconds_between_crawl', default=20, type=int, help='Time in seconds in which no proxy/ip shoul be used twice for crawling. Important to prevent being blacklisted. Default 20 sec')
    parser.add_argument('--max_instances_of_zone', default=4, type=int, help='Quota of GCP for maximum instances per zone')
    parser.add_argument('--instance_name', default="", type=str, help='Name of the instance this script is executed of')
    parser.add_argument('--stop_instance_by_itself', default=0, type=int, help='If 1 instances stops itself if its finished')
    
    # if python file path is in argv remove it 
    if ".py" in argv[0]:
        argv = argv[1:len(argv)]

    time_start = time.time()

    # get all arguments
    args = parser.parse_args(argv)
    marketplace = args.marketplace
    daily = args.daily
    region_space = args.region_space
    api_key = args.telegram_api_key
    chat_id = args.telegram_chatid
    number_running_instances = args.number_running_instances
    number_products = args.number_products
    number_products_total = args.number_products_total
    connection_timeout = args.connection_timeout
    time_break_sec = args.time_break_sec
    seconds_between_crawl = args.seconds_between_crawl
    max_instances_of_zone = args.max_instances_of_zone
    stop_instance_by_itself = args.stop_instance_by_itself
    instance_name = args.instance_name

    zone = utils.get_zone_of_marketplace(marketplace, max_instances_of_zone=max_instances_of_zone, number_running_instances=0,region_space=region_space)
    #zone = "europe-west1-b"
    count_to_crawl = len(get_asin_product_detail_to_crawl(marketplace, daily))
    
    is_first_call = True
    start_size_of_crawling_shirts = len(get_asin_product_detail_to_crawl(marketplace, daily))
    msg_total_shirts_to_crawl = "all"
    if number_products_total != 0:
        msg_total_shirts_to_crawl = str(number_products_total)
    utils.send_msg(chat_id,"%s total shirts to crawl. Script should crawl %s shirts" %(start_size_of_crawling_shirts, msg_total_shirts_to_crawl),api_key)
    
    while True:
        time_wait_minutes = 0
        currently_running_instance = get_currently_running_instance(number_running_instances, marketplace, max_instances_of_zone, region_space)
        currently_running_ids = [int(i.split("-")[-1]) for i in currently_running_instance]
        # if every instance is runnning program sleeps for 5 minutes
        if len(currently_running_instance) == number_running_instances:
            time_wait_minutes = 10
            print("All instances are running. Wait %s minutes..." %str(time_wait_minutes))
            time.sleep(60 * time_wait_minutes)
        # else preemptible logs need to be updated in case of failure and new instance need to be started
        else:
            # check there is still data to crawl
            df_product_detail = get_asin_product_detail_to_crawl(marketplace, daily)
            print("There are %s asins to crawl" % len(df_product_detail))
            # get blacklisted ips 
            blocked_ips = get_blacklisted_ips(marketplace, daily)
            utils.send_msg(chat_id,"%s of %s" %(len(df_product_detail), count_to_crawl),api_key)
            # if no data to crawl exists delete all preemptible instances
            if len(df_product_detail) == 0:
                finish_script(5,seconds_between_crawl,number_products,time_wait_minutes,number_running_instances,marketplace, max_instances_of_zone,region_space,time_start, api_key, chat_id)
                break
            # if number_products_total is set unequal to zero the script should be stoped 
            # if total number of shirts to crawl minus current number of shirts to crawlis greater than number_products_total
            elif number_products_total != 0 and (start_size_of_crawling_shirts - len(df_product_detail)) > number_products_total:
                finish_script(5,seconds_between_crawl,number_products,time_wait_minutes,number_running_instances,marketplace, max_instances_of_zone,region_space,time_start, api_key, chat_id)
                break


            not_running_threat_ids = [x for x in np.arange(1,number_running_instances+1, 1).tolist() if x not in currently_running_ids]
            for id in not_running_threat_ids:
                zone = utils.get_zone_of_marketplace(marketplace, max_instances_of_zone=max_instances_of_zone, number_running_instances=id-1,region_space=region_space)
                pree_id = "thread-" + str(id) + "-" + zone
                # update preemptible logs with failure statement
                if not is_first_call:
                    update_preemptible_logs(pree_id, marketplace, "failure", daily)
                # start instance and startupscript
                start_instance(marketplace, number_running_instances, number_products,connection_timeout, time_break_sec, seconds_between_crawl, pree_id, id, zone, max_instances_of_zone, daily, api_key, chat_id, blocked_ips, region_space, time_start, instance_name, stop_instance_by_itself)
                # before next instance starts 15 seconds should the script wait
                time.sleep(15)       
        is_first_call=False

    if stop_instance_by_itself:
        bashCommand = get_bash_stop_instance(instance_name, "us-west1-b")
        stream = os.popen(bashCommand)
        output = stream.read()


if __name__ == '__main__':
    main(sys.argv)
