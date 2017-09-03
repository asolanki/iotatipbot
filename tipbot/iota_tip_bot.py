import praw
import re
import threading
import time
import queue
import random
import string
from iota import *
from bot_api import api
import logging
import config

#Grab static variables from the config
seed = config.seed

#Initialize the api and reddit
bot_api = api(seed)
reddit = praw.Reddit(
    user_agent=config.user_agent,
    client_id=config.client_id,
    client_secret=config.client_secret,
    username=config.username,
    password=config.password
)

logging.basicConfig(filename='transactionLogs.log',format='%(levelname)s: %(asctime)s: %(message)s ',level=logging.INFO)

#Message links to be appended to every message/comment reply
message_links = "\n\n[Deposit](https://np.reddit.com/message/compose/?to=iotaTipBot&subject=Deposit&message=Deposit iota!) | [Withdraw](https://np.reddit.com/message/compose/?to=iotaTipBot&subject=Withdraw&message=I want to withdraw my iota!\nxxx iota \naddress here) | [Balance](https://np.reddit.com/message/compose/?to=iotaTipBot&subject=Balance&message=I want to check my balance!) | [Help](https://np.reddit.com/message/compose/?to=iotaTipBot&subject=Help!&message=I need help!) | [Donate](https://np.reddit.com/message/compose/?to=iotaTipBot&subject=Donate&message=I want to support iotaTipBot!)\n"

#A thread to handle deposits
#Deposits are handled in 2 phases. 
#   Phase1: A unique 0 balance address is generated and given to the user
#   Phase2: The address is checked for a balance, if the address has a balance grear than 0
#           then the user has deposited to that address and their account should be credited
deposit_queue = queue.Queue()
def deposits():
    bot_api = api(seed)
    deposits = []
    print("Deposit thread started. Waiting for deposits...")

    while True:
        time.sleep(1)
        try:
            #Check the queue for new deposits, add them to the database and local deposit list.
            new_deposit = deposit_queue.get(False)
            bot_api.add_deposit_request(new_deposit)
            deposits.append(new_deposit)
            print("New deposit received: (" + new_deposit['type'] + ", " + new_deposit['reddit_username'] + ")")
        except queue.Empty:
            pass
        for index,deposit in enumerate(deposits):
            deposit_type = deposit['type']
            reddit_username = deposit['reddit_username']
            message = deposit['message']

            if deposit_type == 'address':
                address = bot_api.get_new_address()
                reply = "Please transfer your IOTA to this address:\n{0}\n\nDo not deposit to the same address more than once. This address will expire in 2 hours".format(address._trytes.decode("utf-8"))
                logging.info('{0} was assigned to address {1}'.format(reddit_username,address._trytes.decode("utf-8")))
                message.reply(reply + message_links)
                
                deposit = {'type':'deposit','reddit_username':reddit_username,'address':address,'message':message,'time':time.time()}
                deposit_queue.put(deposit)
                bot_api.remove_deposit_request(deposit)
                del deposits[index]

            elif deposit_type == 'deposit':
                deposit_time = deposit['time']
                #Check if the deposit request has expired
                if (time.time() - deposit_time) > 7200:
                    reply = ('Your deposit request has timed out. Please start a new deposit. Do not transfer to the previous address.')
                    message.reply(reply+message_links)
                    bot_api.remove_deposit_request(deposit)
                    logging.info('{0}\'s deposit has timed out'.format(reddit_username))
                    del deposits[index]
                else:
                    address = deposit['address']
                    reddit_username = deposit['reddit_username']
                    balance = bot_api.get_balance(address)
                    if balance > 0:
                        print("Transaction found, {0} transfered {1} iota".format(reddit_username,balance))
                        bot_api.add_balance(reddit_username,balance)
                        reply = ('You have successfully funded your tipping account with {0} iota'.format(balance))
                        message.reply(reply + message_links)
                        bot_api.remove_deposit_request(deposit)
                        logging.info('{0} deposited {1} iota'.format(reddit_username,balance))
                        del deposits[index]

#Start the deposit thread
deposit_thread = threading.Thread(target=deposits,args = ())
deposit_thread.daemon = True
deposit_thread.start()
                

#This thread handles all withdraw requests
#Withdraw requests are pulled from the queue and executed one by one
withdraw_queue = queue.Queue()
def withdraws():
    bot_api = api(seed)
    withdraws = []
    print("Withdraw thread started. Waiting for withdraws...")
    
    while True:
        time.sleep(1)
        try:
            newWithdraw = withdraw_queue.get(False)    
            withdraws.append(newWithdraw)
            print("New withdraw received: (" + newWithdraw['type'] + ", " + newWithdraw['reddit_username'] + ")")
            print("{0} withdraws in queue".format(withdraw_queue.qsize()))
        except queue.Empty:
                pass
        for index,withdraw in enumerate(withdraws):
            withdrawType = withdraw['type']
            reddit_username = withdraw['reddit_username']
            message = withdraw['message']
            amount = withdraw['amount']
            address = withdraw['address']
            print("Sending transfer to address {0} of amount {1}".format(address,amount))
            bot_api.send_transfer(address,amount)
            print("Transfer complete.")
            logging.info('{0} withdrew {1} iota to address: {2}'.format(reddit_username,amount,address.decode("utf-8")))
            reply = "You have successfully withdrawn {0} IOTA to address {1}".format(amount,address.decode("utf-8"))
            message.reply(reply + message_links)
            bot_api.remove_withdraw_request(withdraw)
            del withdraws[index]

withdrawThread = threading.Thread(target=withdraws,args = ())
withdrawThread.daemon = True
withdrawThread.start()

subreddit = reddit.subreddit('iota+iotaTipBot+IOTAmarkets+IOTAFaucet')
#Monitor all subreddit comments for tips
def monitor_comments():
    bot_api = api(seed)
    comments_replied_to = bot_api.get_comments_replied_to()
    print("Comment thread started. Waiting for comments...")
    while True:
        try:
            for comment in subreddit.stream.comments():
                if not comment.fullname in comments_replied_to:
                    author = comment.author.name
                    if bot_api.is_tip(comment):
                        amount = bot_api.get_iota_tip_amount(comment)
                        if bot_api.check_balance(author,amount):
                            parent_comment = comment.parent()
                            if parent_comment.author is None:
                                continue
                            recipient = parent_comment.author.name
                            bot_api.subtract_balance(author,amount)
                            bot_api.add_balance(recipient,amount)
                            print('Comment Thread: {0} tipped {1}'.format(author,recipient))
                            logging.info('{0} has tipped {1} {2} iota'.format(author,recipient,amount))
                            value = bot_api.get_iota_value(amount)
                            reply = "You have successfully tipped {0} {1} iota(${2}).".format(recipient,amount,'%f' % value)
                            comment.reply(reply + message_links)
                            comments_replied_to.append(comment.fullname)
                            bot_api.add_replied_to_comment(comment.fullname)
                            parent_comment.author.message("You have received a tip!","You received a tip of {0} iota from {1}".format(amount,author))
                        else:
                            reply = "You do not have the required funds."
                            comment.reply(reply + message_links)
                            comments_replied_to.append(comment.fullname)
                            bot_api.add_replied_to_comment(comment.fullname)
        except:
            print("Comment Thread Exception... Restarting...")


comment_thread = threading.Thread(target=monitor_comments,args = ())
comment_thread.daemon = True
comment_thread.start()

def periodic_check():
    print("Periodic Check thread started")
    while True:
        bot_api = api(seed)
        total_balance = bot_api.get_total_balance()
        account_balance = bot_api.get_account_balance()
        difference = account_balance - total_balance
        if total_balance == account_balance:
            print("Periodic Check Thread: Account balance matches total user balance:{0}.".format(account_balance))
            logging.info('Account balance matches total user balance:{0}'.format(account_balance))
        elif total_balance > account_balance:
            print("Periodic Check Thread: Account balance({0}) is less than user balance({1})! Difference: {2}".format(account_balance,total_balance,difference))
            logging.info('Account balance({0}) is less than user balance({1})'.format(account_balance,total_balance))
        elif total_balance < account_balance:
            print("Periodic Check Thread: Account balance({0}) is greater than user balance({1}). Difference: {2}".format(account_balance,total_balance,difference))
            logging.info('Account balance({0}) is greater than user balance({1})'.format(account_balance, total_balance))
        
        used_addresses = bot_api.get_used_addresses()
        address_list = []
        print("Periodic Check Thread: {0} addresses have been used".format(len(used_addresses)))
        for address in used_addresses:
            address_list.append(address[1])
        for address in used_addresses:
            if address_list.count(address[1]) > 1:
                print("Periodic Check Thread: Duplicated address: {0} index {1}".format(address[1],address[0]))
        time.sleep(300)

periodic_check_thread = threading.Thread(target=periodic_check, args = ())
periodic_check_thread.daemon = True
periodic_check_thread.start()

print("Message thread started. Waiting for messages...")


#Reinitiate any requests that were not completed
deposit_requests = bot_api.get_deposit_requests()
withdraw_requests = bot_api.get_withdraw_requests()
for deposit in deposit_requests:
    message_id = deposit[0]
    for message in reddit.inbox.messages():
        if message.fullname == message_id:
            user = message.author.name
            if deposit[1] is None:
                transfer = {'type':'address','reddit_username':user,'message':message,'time':time.time()}
                deposit_queue.put(transfer)
            else:
                transfer = {'type':'deposit','reddit_username':user,'address':Address(deposit[1]),'message':message,'time':time.time()}
                deposit_queue.put(transfer)
            break
for withdraw in withdraw_requests:
    message_id = withdraw[0]
    address = bytearray(withdraw[1],"utf-8")
    amount = withdraw[2]
    for message in reddit.inbox.messages():
        if message.fullname == message_id:
            user = message.author.name
            transfer = {'type':'withdraw','reddit_username':user,'address':address,'amount':amount,'message':message}
            withdraw_queue.put(transfer)


print("Bot initalized.")

#Main loop, Check through messages and comments for requests
while True:
    time.sleep(1)
    try:
        for message in reddit.inbox.messages():
            #print(message.author)
            #print(message.subject)
            #print(message.body)


            #It's a new message, see what it says
            if message.new:
                reddit_username = message.author.name

                #Check if it is a deposit request
                if bot_api.is_deposit_request(message):
                    transfer = {'type':'address','reddit_username':reddit_username, 'message':message,'time': time.time()}
                    deposit_queue.put(transfer)
                    #reply = "Deposits are currently disabled until some issues can be sorted out. Thank you for your patience."
                    #message.reply(reply + message_links)
                    message.mark_read()

                #Check if it is a withdraw request
                elif bot_api.is_withdraw_request(message):

                    #Check how much they want to withdrawl
                    if bot_api.contains_iota_amount(message):
                        amount = bot_api.get_iota_amount(message)
                        if bot_api.check_balance(reddit_username,amount):
                            #Find address
                            address = bot_api.get_message_address(message)
                            if address:
                                bot_api.subtract_balance(reddit_username,amount)
                                transfer = {'type':'withdraw','reddit_username':reddit_username,'address':address,'message':message,'amount':amount,'time': time.time()}
                                withdraw_queue.put(transfer)
                                bot_api.add_withdraw_request(transfer)
                                reply = "Your withdraw has been received and is being processed. Please be patient the withdraw process may take up to a few hours."
                                message.reply(reply + message_links)
                                message.mark_read()
                            else:
                                reply = "You must put the address you want to withdraw to in your message"
                                message.reply(reply + message_links)
                                message.mark_read()
                        else:
                            balance = bot_api.get_user_balance(reddit_username)
                            reply = "Sorry, you don't have {1} IOTA in your account. You currently have {0} IOTA.".format(balance, amount)
                            message.reply(reply + message_links)
                            message.mark_read()
                    else:
                        reply = "You must put the amount of IOTA you want to withdraw in your message. Format: 1024 IOTA"
                        message.reply(reply + message_links)
                        message.mark_read()
    

                #Check if it is a balance request
                elif bot_api.is_balance_request(message):
                    balance = bot_api.get_user_balance(reddit_username)
                    reply = "Your current balance is: {0} iota.".format(balance)
                    message.reply(reply + message_links)
                    message.mark_read()

                elif bot_api.is_help_request(message):
                    reply = "iotaTipBot is a bot that allows reddit users to send iota to each other through reddit comments. The bot commands are as follows:\n\n* 'Deposit' - Initiates the process of depositing iota into your tipping account\n\n* 'Withdraw' - Withdraw iota from your tipping account. You must put the address you want to withdraw to and the amount of iota in the message.\n\n* 'Balance' - Check the amount of iota you have stored in the bot.\n\n* 'Help' - Sends the help message\n\n* 'Donate' - Get a list of options to help support the project.\n\nThese commands are activated by sending the command to the bot either in the subject or the body of the message.\n\nOnce you have iota in your tipping account you can start tipping! To do this simply reply to a comment with a message of the format: '+<amount> iota'\n\nFor example '+25 iota' will tip 25 iota to the author of the comment you replied to. To tip higher values, you can swap the 'iota' part of the comment with 'miota' to tip megaIota values: '+25 miota' will then tip 25 megaIota.\n\nIf you are new to iota and are looking for more information here are a few useful links:\n\n* [Reddit Newcomer Information](https://www.reddit.com/r/Iota/comments/61rc0c/for_newcomers_all_information_links_you_probably/)\n\n* [IOTA Wallet Download](https://github.com/iotaledger/wallet/releases/)\n\n* [Supply and Units Reference](https://i.imgur.com/lsq4610.jpeg)"
                    message.reply(reply + message_links)
                    message.mark_read()
        
                elif bot_api.is_donation_request(message):
                    reply = "Donations help keep this project alive! They help to cover server costs and development time. If you would like to support this project there are many options! Transfer cryptocurrency to one of the addresses below or simply tip the bot! Thank you for your support!\n\nIOTA: IYFJCTTLRIWUWAUB9ZLCRKVKAAQVWHWTENKVVZBXYUPU9YFTBMKFXYWXWESLWJSTBRADUSGVJPVJZCJEZ9IGYDKDJZ\n\nEthereum: 0x254EBc1863FD4eE5F4469b9A18505aF8de958812\n\nBitcoin: 18VhQTN9QcwJNQwMTb2H2AsvCaGsNfzKNK"
                    message.reply(reply+message_links)
                    message.mark_read()
    except:
        print("Message Thread Exception...")
