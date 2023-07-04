"use client"
import styles from '@styles/main.css'
import {Row, Container, Col, Stack} from "react-bootstrap"
import ConfigSideNav from '@components/ConfigSideNav'
import MainContainer from '@components/MainContainer'
import { ToastContainer, toast } from 'react-toastify';
import  { useState } from "react";


export default function Home() {
    // TODO: Clean this up and use Redux
    const [username, setUsername] = useState("None");
    const [modelname, setModelname] = useState("None");
    const [startDate, setStartDate] = useState("1990-01-01");
    const [endDate, setEndDate] = useState("2023-01-01");

    return (
        <>
            <Row className='main-row pe-3 vh-100 overflow-hidden g-0'>
                <Col className="side-bar-col" lg={3} xs={3}>
                        <div className='d-flex align-items-center justify-content-center py-4'><h3>Alpine AI</h3></div>
                        <ConfigSideNav onUser={setUsername} onModel={setModelname} onStartDate={setStartDate} onEndDate={setEndDate}/>
                    <div id="footer" className="justify-content-end">
                        Made in <a href="https://www.alpineai.ch" target="_blank">Switzerland</a>
                    </div>


                </Col>
                <Col lg={9} xs={9} className="main-chat-col mt-3">
                    <MainContainer username={username} modelname={modelname} startDate={startDate} endDate={endDate}/>
                </Col>
                <ToastContainer/>
            </Row>

        </>
    )
}
