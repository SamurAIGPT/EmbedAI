"use client";
import React, {useState} from "react";
import {Button, Stack, Form, Spinner} from "react-bootstrap";
import {ToastContainer, toast} from "react-toastify";

export default function ConfigSideNav() {
    const [isLoading, setIsLoading] = useState(false);
    const [downloadInProgress, setdownloadInProgress] = useState(false);
    const [selectedFile, setSelectedFile] = useState(null);
    const [isUploading, setIsUploading] = useState(null);

    const ingestData = async () => {
        try {
            setIsLoading(true);
            const res = await fetch("http://localhost:8888/ingest");
            const jsonData = await res.json();
            if (!res.ok) {
                // This will activate the closest `error.js` Error Boundary
                console.log("Error Ingesting data");
                setIsLoading(false);
            } else {
                setIsLoading(false);
                console.log(jsonData);
            }
        } catch (error) {
            setIsLoading(false);
            response.text().then(text => {
                toast.error("Error Ingesting data." + text);
            })
        }
    };

    const handleDownloadModel = async () => {
        try {
            setdownloadInProgress(true);
            const res = await fetch("http://localhost:8888/download_model");
            const jsonData = await res.json();
            if (!res.ok) {
                response.text().then(text => {
                    toast.error("Error downloading model." + text);
                })
                setdownloadInProgress(false);
            } else {
                setdownloadInProgress(false);
                toast.success("Model Download complete");
                console.log(jsonData);
            }
        } catch (error) {
            setdownloadInProgress(false);
            console.log(error);
            toast.error("Error downloading model");
        }
    };

    const handleFileChange = (event) => {
        if (event.target.files[0] != null) {
            setSelectedFile(event.target.files[0]);
        }

    };

    const handleUpload = async () => {
        setIsUploading(true)
        try {
            const formData = new FormData();
            formData.append("document", selectedFile);

            const res = await fetch("http://localhost:8888/upload_doc", {
                method: "POST",
                body: formData,
            });

            if (!res.ok) {
                console.log("Error Uploading document");
                response.text().then(text => {
                    toast.error("Error Uploading document." + text);
                })
                setSelectedFile(null); // Clear the selected file after successful upload
                document.getElementById("file-input").value = "";
                setIsUploading(false)
            } else {
                const data = await res.json();
                console.log(data);
                toast.success("Document Upload Successful");
                setSelectedFile(null); // Clear the selected file after successful upload
                document.getElementById("file-input").value = "";
                setIsUploading(false)
            }
        } catch (error) {
            console.log("error");
            toast.error("Error Uploading document");
            setSelectedFile(null); // Clear the selected file after successful upload
            document.getElementById("file-input").value = "";
            setIsUploading(false)
        }
    };

    return (
        <>
            <div className="mx-4 mt-3">
                <Form.Group className="mb-3">
                    <Form.Label>Upload your documents</Form.Label>
                    <Form.Control
                        type="file"
                        size="sm"
                        onChange={handleFileChange}
                        id="file-input"
                    />
                </Form.Group>
                <Stack direction="horizontal" className="mx-4 mt-3" gap={3}>
                    {isUploading ? <div className="d-flex justify-content-center"><Spinner animation="border"/><span
                            className="ms-3">uploading</span></div> :
                        <Button onClick={(e) => handleUpload()}>Upload</Button>}
                    {isLoading ? (
                        <div className="d-flex justify-content-center"><Spinner animation="border"/><span
                            className="ms-3">ingesting</span></div>
                    ) : (
                        <Button onClick={() => ingestData()}>Ingest Data</Button>
                    )}
                </Stack>
            </div>
        </>
    );
}
