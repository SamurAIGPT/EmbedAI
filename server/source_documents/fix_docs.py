import os

path = "Ken/all_documents"
print(os.listdir(path))

root = os.getcwd() + "/" + path
for file in os.listdir(path):

   head, tail = os.path.splitext(file)
   if not tail or tail == ".":
       src = os.path.join(root, file)
       dst = os.path.join(root, head + '.eml')

       print(src, " -> ", dst)

       if not os.path.exists(dst): # check if the file doesn't exist
           os.rename(src, dst)